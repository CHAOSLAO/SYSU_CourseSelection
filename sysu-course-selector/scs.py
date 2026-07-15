import base64
import http.cookiejar
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import socks
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from setting import CONCURRENT_REQUEST, DELAY, SOCKS5_PROXY_PORT, TIMEOUT, USE_SOCKS5_PROXY


class CourseSelectorError(RuntimeError):
    """Raised when SYSU's authentication or course-selection APIs reject a request."""


class course_selector:
    """Client for the current SYSU CAS v3 and JWXT course-selection APIs."""

    COURSE_CATEGORIES = {
        '1': ('专业选修', 1, 21),
        '2': ('公共必修', 1, 10),
        '3': ('体育', 3, 10),
    }

    user_agent = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
    )
    cas_policy_url = 'https://cas.sysu.edu.cn/esc-sso/api/v3/auth/policy'
    cas_login_url = 'https://cas.sysu.edu.cn/esc-sso/api/v3/auth/doLogin'
    cas_sso_url = 'https://cas.sysu.edu.cn/esc-sso/login'
    jwxt_sso_url = 'https://jwxt.sysu.edu.cn/jwxt/api/sso/cas/login?pattern=student-login'
    jwxt_url = 'https://jwxt.sysu.edu.cn/jwxt/{}'
    course_list_path = 'choose-course-front-server/classCourseInfo/course/list'
    course_select_path = 'choose-course-front-server/classCourseInfo/course/choose'
    headers = {
        'User-Agent': user_agent,
        'Accept': 'application/json, text/plain, */*',
    }
    info_paths = (
        'student-status/student-info/detail',
        'choose-course-front-server/classCourseInfo/selectCourseInfo',
        'choose-course-front-server/stuCollectedCourse/getYearTerm',
    )

    def __init__(self):
        logging.basicConfig(
            filename='log3.log',
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)-8s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        self.course_list = []
        self.public_key = None
        self.public_key_id = None
        self.semester_year = None
        self.selected_type = 1
        self.selected_category = 21
        if USE_SOCKS5_PROXY:
            socks.set_default_proxy(socks.SOCKS5, 'localhost', SOCKS5_PROXY_PORT)
            socket.socket = socks.socksocket

    def __open_s(self, request):
        try:
            response = self.opener.open(request, timeout=TIMEOUT)
            content = response.read().decode('utf-8', errors='replace')
            result = {'read': content, 'url': response.geturl(), 'info': response.info()}
            logging.debug('response url=%s body=%s', result['url'], result['read'])
            return result
        except urllib.error.HTTPError as error:
            body = error.read().decode('utf-8', errors='replace')
            logging.debug('http error code=%s reason=%s body=%s', error.code, error.reason, body)
            return {'read': body, 'code': error.code, 'reason': error.reason}
        except (socket.timeout, urllib.error.URLError) as error:
            logging.debug('network error: %s', error)
            return None

    def __api_headers(self):
        return {
            **self.headers,
            'Content-Type': 'application/json;charset=UTF-8',
            'Origin': 'https://jwxt.sysu.edu.cn',
            'Referer': 'https://jwxt.sysu.edu.cn/jwxt/mk/courseSelection/',
        }

    @staticmethod
    def __json(body, action):
        try:
            return json.loads(body)
        except (TypeError, json.JSONDecodeError) as error:
            raise CourseSelectorError('{} returned an unexpected response.'.format(action)) from error

    def __request_json(self, path, action, payload=None, accepted_codes=(200,)):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            self.jwxt_url.format(path), data=data, headers=self.__api_headers()
        )
        response = self.__open_s(request)
        if response is None or 'code' in response:
            raise CourseSelectorError('{} failed: unable to reach JWXT.'.format(action))
        result = self.__json(response['read'], action)
        if result.get('code') not in accepted_codes:
            message = result.get('message') or result.get('data') or 'unknown JWXT error'
            raise CourseSelectorError('{} failed: {}'.format(action, message))
        return result

    def pre_login(self):
        """Fetch the public key required by the current CAS v3 login API."""
        request = urllib.request.Request(self.cas_policy_url, headers=self.headers)
        response = self.__open_s(request)
        if response is None or 'code' in response:
            raise CourseSelectorError('Unable to reach the CAS login policy endpoint.')
        policy = self.__json(response['read'], 'CAS login policy')
        try:
            params = policy['data']['param']
            public_key = params['publicKey']
            self.public_key_id = params['publicKeyId']
            self.public_key = serialization.load_der_public_key(base64.b64decode(public_key))
        except (KeyError, TypeError, ValueError) as error:
            raise CourseSelectorError('CAS returned an unsupported login policy.') from error

    def in_login(self, username, password):
        """Log in through CAS v3, then create the JWXT student session."""
        if self.public_key is None or self.public_key_id is None:
            self.pre_login()
        if not username or username == 'YOUR_NET_ID' or not password or password == 'YOUR_PASSWORD':
            raise CourseSelectorError('Set your NetID and password in info.py before running the program.')

        encrypted_password = base64.b64encode(
            self.public_key.encrypt(password.encode('utf-8'), padding.PKCS1v15())
        ).decode('ascii')
        payload = {
            'authType': 'webLocalAuth',
            'dataField': {
                'username': username,
                'password': encrypted_password,
                'publicKeyId': self.public_key_id,
            },
        }
        request = urllib.request.Request(
            self.cas_login_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={**self.headers, 'Content-Type': 'application/json'},
        )
        response = self.__open_s(request)
        if response is None or 'code' in response:
            raise CourseSelectorError('Unable to submit the CAS login request.')
        result = self.__json(response['read'], 'CAS login')
        if str(result.get('code')) != '0':
            message = result.get('message') or result.get('msg') or 'invalid credentials or additional verification required'
            raise CourseSelectorError('CAS login failed: {}'.format(message))

        # CAS now issues the JWXT ticket through this student-login SSO endpoint.
        service = urllib.parse.quote(self.jwxt_sso_url, safe='')
        sso_request = urllib.request.Request(
            '{}?service={}'.format(self.cas_sso_url, service), headers=self.headers
        )
        sso_response = self.__open_s(sso_request)
        if sso_response is None or 'code' in sso_response:
            raise CourseSelectorError('CAS login succeeded, but the JWXT SSO session could not be created.')
        if 'mfaLogin' in sso_response.get('url', ''):
            raise CourseSelectorError('Additional CAS verification is required; complete it in a browser, then retry.')
        self.post_login()

    def post_login(self):
        """Confirm the JWXT session and obtain the active course-selection term."""
        for path in self.info_paths:
            result = self.__request_json(path, 'JWXT session initialization')
            if path.endswith('selectCourseInfo'):
                self.semester_year = (result.get('data') or {}).get('semesterYear')
        if not self.semester_year:
            raise CourseSelectorError('JWXT did not return an active course-selection term.')

    def course_query(self, selected_type=1, selected_category=21):
        if not self.semester_year:
            raise CourseSelectorError('Log in before querying courses.')
        self.selected_type = int(selected_type)
        self.selected_category = int(selected_category)
        payload = {
            'pageNo': 1,
            'pageSize': 20,
            'param': {
                'semesterYear': self.semester_year,
                'selectedType': str(self.selected_type),
                'selectedCate': str(self.selected_category),
                'hiddenConflictStatus': '0',
                'hiddenSelectedStatus': '0',
                'collectionStatus': '0',
            },
        }
        result = self.__request_json(self.course_list_path, 'Course query', payload)
        course_data = (result.get('data') or {}).get('rows') or []
        self.course_list = [
            {
                **item,
                '_selected_type': self.selected_type,
                '_selected_category': self.selected_category,
            }
            for item in course_data
        ]
        return [{
            'cid': item.get('courseNum', ''),
            'cname': item.get('courseName', ''),
            'lecturer': (item.get('teachingTimePlace') or '').split(';')[0],
            'sid': item.get('teachingClassId', ''),
            'snum': '{}/{}'.format(item.get('courseSelectedNum', 0), item.get('baseReceiveNum', 0)),
            'status': item.get('selectedStatus') == 4 or item.get('selectedStatus') == '4',
        } for item in course_data]

    def course_query_categories(self, category_keys):
        """Query one or more configured categories and retain each class's selection metadata."""
        all_courses = []
        displayed_courses = []
        for category_key in category_keys:
            _, selected_type, selected_category = self.COURSE_CATEGORIES[category_key]
            displayed_courses.extend(self.course_query(selected_type, selected_category))
            all_courses.extend(self.course_list)
        self.course_list = all_courses
        return displayed_courses

    class course_select_thread(threading.Thread):
        def __init__(self, selector, select_id, selected_type, selected_category):
            super().__init__()
            self.selector = selector
            self.select_id = select_id
            self.selected_type = selected_type
            self.selected_category = selected_category

        def run(self):
            self.selector.course_select(self.select_id, self.selected_type, self.selected_category)

    def course_select(self, select_id, selected_type, selected_category):
        payload = {
            'clazzId': str(select_id),
            'selectedType': str(selected_type),
            'selectedCate': str(selected_category),
            'check': True,
        }
        while True:
            try:
                result = self.__request_json(
                    self.course_select_path,
                    'Course selection',
                    payload,
                    accepted_codes=(200, 52021104),
                )
            except CourseSelectorError as error:
                logging.warning('course selection request failed: %s', error)
                time.sleep(DELAY)
                continue
            if result.get('code') == 200 or result.get('code') == 52021104:
                print('Course selected (or already selected); stopping this request thread.')
                return
            time.sleep(DELAY)

    def course_select_wrapper(self, target_course_list_str):
        course_ids = [course_id.strip() for course_id in target_course_list_str.split(',') if course_id.strip()]
        matching_courses = [
            item for item in self.course_list if item.get('courseNum') in course_ids
        ]
        missing = set(course_ids) - {item.get('courseNum') for item in matching_courses}
        if missing:
            raise CourseSelectorError('Course IDs not found in the current query: {}'.format(', '.join(sorted(missing))))

        threads = []
        for item in matching_courses:
            for _ in range(CONCURRENT_REQUEST):
                thread = self.course_select_thread(
                    self,
                    item['teachingClassId'],
                    item.get('_selected_type', self.selected_type),
                    item.get('_selected_category', self.selected_category),
                )
                thread.start()
                threads.append(thread)
        for thread in threads:
            thread.join()
