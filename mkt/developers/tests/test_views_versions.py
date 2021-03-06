import mock
from nose.tools import eq_
from pyquery import PyQuery as pq

import amo
import amo.tests
from addons.models import Addon, AddonUser
from devhub.models import ActivityLog
from users.models import UserProfile
from versions.models import Version

from mkt.submit.tests.test_views import BasePackagedAppTest


class TestVersion(amo.tests.TestCase):
    fixtures = ['base/apps', 'base/users', 'webapps/337141-steamcube']

    def setUp(self):
        self.client.login(username='admin@mozilla.com', password='password')
        self.webapp = self.get_webapp()
        self.url = self.webapp.get_dev_url('versions')

    def get_webapp(self):
        return Addon.objects.get(id=337141)

    def test_nav_link(self):
        r = self.client.get(self.url)
        eq_(pq(r.content)('#edit-addon-nav li.selected a').attr('href'),
            self.url)

    def test_items(self):
        doc = pq(self.client.get(self.url).content)
        eq_(doc('#version-status').length, 1)
        eq_(doc('#version-list').length, 0)
        eq_(doc('#delete-addon').length, 0)
        eq_(doc('#modal-delete').length, 0)
        eq_(doc('#modal-disable').length, 1)
        eq_(doc('#modal-delete-version').length, 0)

    def test_soft_delete_items(self):
        self.create_switch(name='soft_delete')
        doc = pq(self.client.get(self.url).content)
        eq_(doc('#version-status').length, 1)
        eq_(doc('#version-list').length, 0)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#modal-delete').length, 1)
        eq_(doc('#modal-disable').length, 1)
        eq_(doc('#modal-delete-version').length, 0)

    def test_delete_link(self):
        # Hard "Delete App" link should be visible for only incomplete apps.
        self.webapp.update(status=amo.STATUS_NULL)
        doc = pq(self.client.get(self.url).content)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#modal-delete').length, 1)

    def test_pending(self):
        self.webapp.update(status=amo.STATUS_PENDING)
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(doc('#version-status .status-pending').length, 1)
        eq_(doc('#rejection').length, 0)

    def test_public(self):
        eq_(self.webapp.status, amo.STATUS_PUBLIC)
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(doc('#version-status .status-public').length, 1)
        eq_(doc('#rejection').length, 0)

    def test_blocked(self):
        self.webapp.update(status=amo.STATUS_BLOCKED)
        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        doc = pq(r.content)
        eq_(doc('#version-status .status-blocked').length, 1)
        eq_(doc('#rejection').length, 0)
        assert 'blocked by a site administrator' in doc.text()

    def test_rejected(self):
        comments = "oh no you di'nt!!"
        amo.set_user(UserProfile.objects.get(username='editor'))
        amo.log(amo.LOG.REJECT_VERSION, self.webapp,
                self.webapp.current_version, user_id=999,
                details={'comments': comments, 'reviewtype': 'pending'})
        self.webapp.update(status=amo.STATUS_REJECTED)
        (self.webapp.versions.latest()
                             .all_files[0].update(status=amo.STATUS_DISABLED))

        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        doc = pq(r.content)('#version-status')
        eq_(doc('.status-rejected').length, 1)
        eq_(doc('#rejection').length, 1)
        eq_(doc('#rejection blockquote').text(), comments)

        my_reply = 'fixed just for u, brah'
        r = self.client.post(self.url, {'notes': my_reply,
                                        'resubmit-app': ''})
        self.assertRedirects(r, self.url, 302)

        webapp = self.get_webapp()
        eq_(webapp.status, amo.STATUS_PENDING,
            'Reapplied apps should get marked as pending')
        eq_(webapp.versions.latest().all_files[0].status, amo.STATUS_PENDING,
            'Files for reapplied apps should get marked as pending')
        eq_(unicode(webapp.versions.all()[0].approvalnotes), my_reply)

    def test_rejected_packaged(self):
        self.webapp.update(is_packaged=True)
        comments = "oh no you di'nt!!"
        amo.set_user(UserProfile.objects.get(username='editor'))
        amo.log(amo.LOG.REJECT_VERSION, self.webapp,
                self.webapp.current_version, user_id=999,
                details={'comments': comments, 'reviewtype': 'pending'})
        self.webapp.update(status=amo.STATUS_REJECTED)
        (self.webapp.versions.latest()
                             .all_files[0].update(status=amo.STATUS_DISABLED))

        r = self.client.get(self.url)
        eq_(r.status_code, 200)
        doc = pq(r.content)('#version-status')
        eq_(doc('.status-rejected').length, 1)
        eq_(doc('#rejection').length, 1)
        eq_(doc('#rejection blockquote').text(), comments)


class TestAddVersion(BasePackagedAppTest):

    def setUp(self):
        super(TestAddVersion, self).setUp()
        self.app = amo.tests.app_factory(is_packaged=True,
                                         version_kw=dict(version='1.0'))
        self.url = self.app.get_dev_url('versions')
        self.user = UserProfile.objects.get(username='regularuser')
        AddonUser.objects.create(user=self.user, addon=self.app)

    def _post(self, expected_status=200):
        res = self.client.post(self.url, {'upload': self.upload.pk,
                                          'upload-version': ''})
        eq_(res.status_code, expected_status)
        return res

    def test_post(self):
        self.app.current_version.update(version='0.9',
                                        created=self.days_ago(1))
        self._post(302)
        version = self.app.versions.latest()
        eq_(version.version, '1.0')
        eq_(version.all_files[0].status, amo.STATUS_PENDING)

    def test_unique_version(self):
        res = self._post(200)
        self.assertFormError(res, 'upload_form', 'upload',
                             'Version 1.0 already exists')


class TestEditVersion(amo.tests.TestCase):
    fixtures = ['base/users']

    def setUp(self):
        self.app = amo.tests.app_factory(is_packaged=True,
                                         version_kw=dict(version='1.0'))
        version = self.app.current_version
        self.url = self.app.get_dev_url('versions.edit', [version.pk])
        self.user = UserProfile.objects.get(username='regularuser')
        AddonUser.objects.create(user=self.user, addon=self.app)
        self.client.login(username='regular@mozilla.com',
                          password='password')
        eq_(self.client.get(self.url).status_code, 200)

    def test_post(self):
        rn = u'Release Notes'
        an = u'Approval Notes'
        res = self.client.post(self.url, {'releasenotes': rn,
                                          'approvalnotes': an})
        self.assert3xx(res, self.app.get_dev_url('versions'))
        ver = self.app.versions.latest()
        eq_(ver.releasenotes, rn)
        eq_(ver.approvalnotes, an)


class TestVersionPackaged(amo.tests.WebappTestCase):
    fixtures = ['base/apps', 'base/users', 'webapps/337141-steamcube']

    def setUp(self):
        super(TestVersionPackaged, self).setUp()
        assert self.client.login(username='steamcube@mozilla.com',
                                 password='password')
        self.app.update(is_packaged=True)
        self.app = self.get_app()
        self.url = self.app.get_dev_url('versions')
        self.delete_url = self.app.get_dev_url('versions.delete')

    def test_items_packaged(self):
        self.create_switch(name='soft_delete')
        doc = pq(self.client.get(self.url).content)
        eq_(doc('#version-status').length, 1)
        eq_(doc('#version-list').length, 1)
        eq_(doc('#delete-addon').length, 1)
        eq_(doc('#modal-delete').length, 1)
        eq_(doc('#modal-disable').length, 1)
        eq_(doc('#modal-delete-version').length, 1)

    def test_version_list_packaged(self):
        self.app.update(is_packaged=True)
        amo.tests.version_factory(addon=self.app, version='2.0',
                                  file_kw=dict(status=amo.STATUS_PENDING))
        self.app = self.get_app()
        doc = pq(self.client.get(self.url).content)
        eq_(doc('#version-status').length, 1)
        eq_(doc('#version-list li').length, 2)
        # 1 pending and 1 public.
        eq_(doc('#version-list span.status-pending').length, 1)
        eq_(doc('#version-list span.status-public').length, 1)
        # Check version strings and order of versions.
        eq_(map(lambda x: x.text, doc('#version-list h4 a')),
            ['Version 2.0', 'Version 1.0'])
        # There should be 2 delete buttons.
        eq_(doc('#version-list a.delete-version').length, 2)
        # Check download url.
        eq_(doc('#version-list a.button.download').eq(0).attr('href'),
            self.app.versions.all()[0].all_files[0].get_url_path('devhub'))
        eq_(doc('#version-list a.button.download').eq(1).attr('href'),
            self.app.versions.all()[1].all_files[0].get_url_path('devhub'))

    def test_delete_version(self):
        version = self.app.versions.latest()
        version.update(version='<script>alert("xss")</script>')
        res = self.client.get(self.url)
        assert not '<script>alert(' in res.content
        assert '&lt;script&gt;alert(' in res.content
        # Now do the POST to delete.
        res = self.client.post(self.delete_url, dict(version_id=version.pk),
                               follow=True)
        assert not Version.objects.filter(pk=version.pk).exists()
        eq_(ActivityLog.objects.filter(action=amo.LOG.DELETE_VERSION.id)
                               .count(), 1)
        # Since this was the last version, the app status should be incomplete.
        eq_(self.get_app().status, amo.STATUS_NULL)
        # Check xss in success flash message.
        assert not '<script>alert(' in res.content
        assert '&lt;script&gt;alert(' in res.content

    def test_anonymous_delete_redirects(self):
        self.client.logout()
        version = self.app.versions.latest()
        res = self.client.post(self.delete_url, dict(version_id=version.pk))
        self.assertLoginRedirects(res, self.delete_url)

    def test_non_author_no_delete_for_you(self):
        self.client.logout()
        assert self.client.login(username='regular@mozilla.com',
                                 password='password')
        version = self.app.versions.latest()
        res = self.client.post(self.delete_url, dict(version_id=version.pk))
        eq_(res.status_code, 403)

    @mock.patch.object(Version, 'delete')
    def test_roles_and_delete(self, mock_version):
        user = UserProfile.objects.get(email='regular@mozilla.com')
        addon_user = AddonUser.objects.create(user=user, addon=self.app)
        allowed = [amo.AUTHOR_ROLE_OWNER, amo.AUTHOR_ROLE_DEV]
        for role in [r[0] for r in amo.AUTHOR_CHOICES]:
            self.client.logout()
            addon_user.role = role
            addon_user.save()
            assert self.client.login(username='regular@mozilla.com',
                                     password='password')
            version = self.app.versions.latest()
            res = self.client.post(self.delete_url,
                                   dict(version_id=version.pk))
            if role in allowed:
                self.assert3xx(res, self.url)
                assert mock_version.called, ('Unexpected: `Version.delete` '
                                             'should have been called.')
                mock_version.reset_mock()
            else:
                eq_(res.status_code, 403)
