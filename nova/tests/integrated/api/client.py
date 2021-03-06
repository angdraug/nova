#    Copyright (c) 2011 Justin Santa Barbara
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import urllib

from oslo.serialization import jsonutils
import requests

from nova.i18n import _
from nova.openstack.common import log as logging
from nova.tests.image import fake


LOG = logging.getLogger(__name__)


class OpenStackApiException(Exception):
    def __init__(self, message=None, response=None):
        self.response = response
        if not message:
            message = 'Unspecified error'

        if response:
            _status = response.status_code
            _body = response.content

            message = (_('%(message)s\nStatus Code: %(_status)s\n'
                         'Body: %(_body)s') %
                         {'message': message, '_status': _status,
                          '_body': _body})

        super(OpenStackApiException, self).__init__(message)


class OpenStackApiAuthenticationException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Authentication error")
        super(OpenStackApiAuthenticationException, self).__init__(message,
                                                                  response)


class OpenStackApiAuthorizationException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Authorization error")
        super(OpenStackApiAuthorizationException, self).__init__(message,
                                                                  response)


class OpenStackApiNotFoundException(OpenStackApiException):
    def __init__(self, response=None, message=None):
        if not message:
            message = _("Item not found")
        super(OpenStackApiNotFoundException, self).__init__(message, response)


class TestOpenStackClient(object):
    """Simple OpenStack API Client.

    This is a really basic OpenStack API client that is under our control,
    so we can make changes / insert hooks for testing

    """

    def __init__(self, auth_user, auth_key, auth_uri):
        super(TestOpenStackClient, self).__init__()
        self.auth_result = None
        self.auth_user = auth_user
        self.auth_key = auth_key
        self.auth_uri = auth_uri
        # default project_id
        self.project_id = 'openstack'

    def request(self, url, method='GET', body=None, headers=None):
        _headers = {'Content-Type': 'application/json'}
        _headers.update(headers or {})

        response = requests.request(method, url, data=body, headers=_headers)
        return response

    def _authenticate(self):
        if self.auth_result:
            return self.auth_result

        auth_uri = self.auth_uri
        headers = {'X-Auth-User': self.auth_user,
                   'X-Auth-Key': self.auth_key,
                   'X-Auth-Project-Id': self.project_id}
        response = self.request(auth_uri,
                                headers=headers)

        http_status = response.status_code
        LOG.debug("%(auth_uri)s => code %(http_status)s",
                  {'auth_uri': auth_uri, 'http_status': http_status})

        if http_status == 401:
            raise OpenStackApiAuthenticationException(response=response)

        self.auth_result = response.headers
        return self.auth_result

    def api_request(self, relative_uri, check_response_status=None,
                    strip_version=False, **kwargs):
        auth_result = self._authenticate()

        # NOTE(justinsb): httplib 'helpfully' converts headers to lower case
        base_uri = auth_result['x-server-management-url']
        if strip_version:
            # NOTE(vish): cut out version number and tenant_id
            base_uri = '/'.join(base_uri.split('/', 3)[:-1])

        full_uri = '%s/%s' % (base_uri, relative_uri)

        headers = kwargs.setdefault('headers', {})
        headers['X-Auth-Token'] = auth_result['x-auth-token']

        response = self.request(full_uri, **kwargs)

        http_status = response.status_code
        LOG.debug("%(relative_uri)s => code %(http_status)s",
                  {'relative_uri': relative_uri, 'http_status': http_status})

        if check_response_status:
            if http_status not in check_response_status:
                if http_status == 404:
                    raise OpenStackApiNotFoundException(response=response)
                elif http_status == 401:
                    raise OpenStackApiAuthorizationException(response=response)
                else:
                    raise OpenStackApiException(
                                        message=_("Unexpected status code"),
                                        response=response)

        return response

    def _decode_json(self, response):
        body = response.content
        LOG.debug("Decoding JSON: %s", body)
        if body:
            return jsonutils.loads(body)
        else:
            return ""

    def api_get(self, relative_uri, **kwargs):
        kwargs.setdefault('check_response_status', [200])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_post(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'POST'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_put(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'PUT'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202, 204])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_delete(self, relative_uri, **kwargs):
        kwargs['method'] = 'DELETE'
        kwargs.setdefault('check_response_status', [200, 202, 204])
        return self.api_request(relative_uri, **kwargs)

    def get_server(self, server_id):
        return self.api_get('/servers/%s' % server_id)['server']

    def get_servers(self, detail=True, search_opts=None):
        rel_url = '/servers/detail' if detail else '/servers'

        if search_opts is not None:
            qparams = {}
            for opt, val in search_opts.iteritems():
                qparams[opt] = val
            if qparams:
                query_string = "?%s" % urllib.urlencode(qparams)
                rel_url += query_string
        return self.api_get(rel_url)['servers']

    def post_server(self, server):
        response = self.api_post('/servers', server)
        if 'reservation_id' in response:
            return response
        else:
            return response['server']

    def put_server(self, server_id, server):
        return self.api_put('/servers/%s' % server_id, server)

    def post_server_action(self, server_id, data):
        return self.api_post('/servers/%s/action' % server_id, data)

    def delete_server(self, server_id):
        return self.api_delete('/servers/%s' % server_id)

    def get_image(self, image_id):
        return self.api_get('/images/%s' % image_id)['image']

    def get_images(self, detail=True):
        rel_url = '/images/detail' if detail else '/images'
        return self.api_get(rel_url)['images']

    def post_image(self, image):
        return self.api_post('/images', image)['image']

    def delete_image(self, image_id):
        return self.api_delete('/images/%s' % image_id)

    def get_flavor(self, flavor_id):
        return self.api_get('/flavors/%s' % flavor_id)['flavor']

    def get_flavors(self, detail=True):
        rel_url = '/flavors/detail' if detail else '/flavors'
        return self.api_get(rel_url)['flavors']

    def post_flavor(self, flavor):
        return self.api_post('/flavors', flavor)['flavor']

    def delete_flavor(self, flavor_id):
        return self.api_delete('/flavors/%s' % flavor_id)

    def get_volume(self, volume_id):
        return self.api_get('/volumes/%s' % volume_id)['volume']

    def get_volumes(self, detail=True):
        rel_url = '/volumes/detail' if detail else '/volumes'
        return self.api_get(rel_url)['volumes']

    def post_volume(self, volume):
        return self.api_post('/volumes', volume)['volume']

    def delete_volume(self, volume_id):
        return self.api_delete('/volumes/%s' % volume_id)

    def get_server_volume(self, server_id, attachment_id):
        return self.api_get('/servers/%s/os-volume_attachments/%s' %
                            (server_id, attachment_id))['volumeAttachment']

    def get_server_volumes(self, server_id):
        return self.api_get('/servers/%s/os-volume_attachments' %
                            (server_id))['volumeAttachments']

    def post_server_volume(self, server_id, volume_attachment):
        return self.api_post('/servers/%s/os-volume_attachments' %
                             (server_id), volume_attachment
                            )['volumeAttachment']

    def delete_server_volume(self, server_id, attachment_id):
        return self.api_delete('/servers/%s/os-volume_attachments/%s' %
                            (server_id, attachment_id))


class TestOpenStackClientV3(TestOpenStackClient):
    """Simple OpenStack v3 API Client.

    This is a really basic OpenStack API client that is under our control,
    so we can make changes / insert hooks for testing.

    Note that the V3 API does not have an image API and so it is
    not possible to query the api for the image information.
    So instead we just access the fake image service used by the unittests
    directly.

    """

    def get_image(self, image_id):
        return fake._fakeImageService.show(None, image_id)

    def get_images(self, detail=True):
        return fake._fakeImageService.detail(None)

    def post_image(self, image):
        raise NotImplementedError

    def delete_image(self, image_id):
        return fake._fakeImageService.delete(None, image_id)


class TestOpenStackClientV3Mixin(object):
    def _get_test_client(self):
        return TestOpenStackClientV3('fake', 'fake', self.auth_url)
