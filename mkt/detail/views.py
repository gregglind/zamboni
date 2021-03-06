import hashlib
import json
import os

from django import http
from django.conf import settings
from django.shortcuts import redirect
from django.views.decorators.http import etag

import commonware.log
import jingo
from session_csrf import anonymous_csrf_exempt
from tower import ugettext as _

import amo
from abuse.models import send_abuse_report
from access import acl
from addons.decorators import addon_view_factory
from amo.decorators import login_required, permission_required
from amo.helpers import absolutify
from amo.forms import AbuseForm
from amo.utils import JSONEncoder, paginate
from devhub.models import ActivityLog
from reviews.models import GroupedRating, Review
from reviews.views import get_flags

from mkt.site import messages
from mkt.webapps.models import Webapp

log = commonware.log.getLogger('z.detail')

addon_view = addon_view_factory(qs=Webapp.objects.valid)
addon_all_view = addon_view_factory(qs=Webapp.objects.all)


@addon_all_view
def detail(request, addon):
    """Product details page."""
    reviews = Review.objects.latest().filter(addon=addon)
    ctx = {
        'product': addon,
        'reviews': reviews[:2],
        'flags': get_flags(request, reviews),
        'has_review': request.user.is_authenticated() and
                      reviews.filter(user=request.user.id).exists(),
        'grouped_ratings': GroupedRating.get(addon.id),
        'details_page': True
    }
    if addon.is_public():
        ctx['abuse_form'] = AbuseForm(request=request)
    return jingo.render(request, 'detail/app.html', ctx)


@addon_all_view
def manifest(request, addon):
    """
    Returns the "mini" manifest for packaged apps.

    If not a packaged app, returns an empty JSON doc.

    """
    is_reviewer = acl.check_reviewer(request)
    is_dev = addon.has_author(request.amo_user)
    is_public = addon.status == amo.STATUS_PUBLIC

    # If webapp is blocklisted, show the blocklisted manifest.
    if addon.status == amo.STATUS_BLOCKED:
        # TODO: Consider caching the os.stat call to avoid FS hits.
        package_name = 'packaged-apps/blocklisted.zip'
        data = {
            'name': addon.name,
            'size': os.stat(os.path.join(settings.MEDIA_ROOT,
                                         package_name)).st_size,
            'release_notes':
                _(u'This app has been blocked for your protection.'),
            'package_path': absolutify(os.path.join(settings.MEDIA_URL,
                                                    package_name)),
        }
        manifest_content = json.dumps(data, cls=JSONEncoder)
        manifest_etag = hashlib.md5(manifest_content).hexdigest()

        log.info('Serving up blocklisted app for addon: %s' % addon)

    elif (not addon.is_packaged or addon.disabled_by_user or (
              not is_public and not (is_reviewer or is_dev))):
        raise http.Http404

    else:
        manifest_content = addon.get_cached_manifest()
        manifest_etag = hashlib.md5(manifest_content).hexdigest()

    @etag(lambda r, a: manifest_etag)
    def _inner_view(request, addon):
        response = http.HttpResponse(manifest_content,
            content_type='application/x-web-app-manifest+json')
        response['ETag'] = manifest_etag
        return response

    return _inner_view(request, addon)


@addon_all_view
def privacy(request, addon):
    is_dev = request.check_ownership(addon, require_owner=False,
                                     ignore_disabled=True)
    if not (addon.is_public() or acl.check_reviewer(request) or is_dev):
        raise http.Http404
    if not addon.privacy_policy:
        return http.HttpResponseRedirect(addon.get_url_path())
    return jingo.render(request, 'detail/privacy.html', {'product': addon})


@anonymous_csrf_exempt
@addon_view
def abuse(request, addon):
    form = AbuseForm(request.POST or None, request=request)
    if request.method == 'POST' and form.is_valid():
        send_abuse_report(request, addon, form.cleaned_data['text'])
        messages.success(request, _('Abuse reported.'))
        return redirect(addon.get_url_path())
    else:
        return jingo.render(request, 'detail/abuse.html',
                            {'product': addon, 'abuse_form': form})


@anonymous_csrf_exempt
@addon_view
def abuse_recaptcha(request, addon):
    form = AbuseForm(request.POST or None, request=request)
    if request.method == 'POST' and form.is_valid():
        send_abuse_report(request, addon, form.cleaned_data['text'])
        messages.success(request, _('Abuse reported.'))
        return redirect(addon.get_url_path())
    else:
        return jingo.render(request, 'detail/abuse_recaptcha.html',
                            {'product': addon, 'abuse_form': form})


@login_required
@permission_required('AccountLookup', 'View')
@addon_all_view
def app_activity(request, addon):
    """Shows the app activity age for single app."""

    user_items = ActivityLog.objects.for_apps([addon]).exclude(
        action__in=amo.LOG_HIDE_DEVELOPER)
    admin_items = ActivityLog.objects.for_apps([addon]).filter(
        action__in=amo.LOG_HIDE_DEVELOPER)

    user_items = paginate(request, user_items, per_page=20)
    admin_items = paginate(request, admin_items, per_page=20)

    return jingo.render(request, 'detail/app_activity.html',
                        {'admin_items': admin_items,
                         'product': addon,
                         'user_items': user_items})
