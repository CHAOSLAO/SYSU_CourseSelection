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


class CourseSelectionFailure(CourseSelectorError):
    """A course-selection rejection with a user-facing category and retry rule."""

    def __init__(self, code, message, reason, retryable):
        self.code = str(code)
        self.message = str(message)
        self.reason = reason
        self.retryable = retryable
        super().__init__('{}（{}）'.format(reason, self.message))


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
    course_back_path = 'choose-course-front-server/classCourseInfo/course/back'
    selected_course_list_path = 'choose-course-front-server/selectedCourse/list'
    sports_volunteer_list_path = 'choose-course-front-server/selectedCourse/sportsSelectedlist'
    sports_volunteer_update_path = 'choose-course-front-server/selectedCourse/updateSportsSelectedlist'
    headers = {
        'User-Agent': user_agent,
        'Accept': 'application/json, text/plain, */*',
    }
    info_paths = (
        'student-status/student-info/detail',
        'choose-course-front-server/classCourseInfo/selectCourseInfo',
        'choose-course-front-server/stuCollectedCourse/getYearTerm',
    )

    def __init__(
        self,
        concurrent_request=CONCURRENT_REQUEST,
        delay=DELAY,
        timeout=TIMEOUT,
        use_socks5_proxy=USE_SOCKS5_PROXY,
        socks5_proxy_port=SOCKS5_PROXY_PORT,
    ):
        if not 1 <= int(concurrent_request) <= 10:
            raise CourseSelectorError('Concurrent request count must be between 1 and 10.')
        if not 1 <= int(delay) <= 60:
            raise CourseSelectorError('Retry interval must be between 1 and 60 seconds.')
        if not 2 <= int(timeout) <= 60:
            raise CourseSelectorError('Network timeout must be between 2 and 60 seconds.')
        if use_socks5_proxy and not 1 <= int(socks5_proxy_port) <= 65535:
            raise CourseSelectorError('SOCKS5 proxy port must be between 1 and 65535.')
        logging.basicConfig(
            filename='log3.log',
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)-8s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        self.course_list = []
        self.class_number_by_id = {}
        self.public_key = None
        self.public_key_id = None
        self.semester_year = None
        self.selection_stage = {}
        self.event_callback = None
        self.selection_stop_event = threading.Event()
        self.selected_type = 1
        self.selected_category = 21
        self.concurrent_request = int(concurrent_request)
        self.delay = int(delay)
        self.timeout = int(timeout)
        self.use_socks5_proxy = bool(use_socks5_proxy)
        self.socks5_proxy_port = int(socks5_proxy_port)
        if self.use_socks5_proxy:
            socks.set_default_proxy(socks.SOCKS5, 'localhost', self.socks5_proxy_port)
            socket.socket = socks.socksocket

    def __open_s(self, request):
        try:
            response = self.opener.open(request, timeout=self.timeout)
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

    def __emit_event(self, event_type, **details):
        """Report background selection activity to an optional UI observer."""
        callback = self.event_callback
        if callback is None:
            return
        try:
            callback({'type': event_type, **details})
        except Exception as error:
            logging.warning('selection event callback failed: %s', error)

    def stop_course_selection(self):
        """Stop all active retry loops after their current request returns."""
        self.selection_stop_event.set()
        self.__emit_event('stopping')

    @staticmethod
    def __json(body, action):
        try:
            return json.loads(body)
        except (TypeError, json.JSONDecodeError) as error:
            raise CourseSelectorError('{} returned an unexpected response.'.format(action)) from error

    def __request_json(self, path, action, payload=None, accepted_codes=(200,), error_factory=None):
        data = None if payload is None else json.dumps(payload).encode('utf-8')
        request = urllib.request.Request(
            self.jwxt_url.format(path), data=data, headers=self.__api_headers()
        )
        response = self.__open_s(request)
        if response is None or 'code' in response:
            raise CourseSelectorError('{} failed: unable to reach JWXT.'.format(action))
        result = self.__json(response['read'], action)
        if result.get('code') not in accepted_codes:
            if error_factory is not None:
                raise error_factory(result)
            message = result.get('message') or result.get('data') or 'unknown JWXT error'
            raise CourseSelectorError('{} failed: {}'.format(action, message))
        return result

    @staticmethod
    def __selection_failure(result):
        """Classify the official JWXT rejection codes for useful UI feedback."""
        code = str(result.get('code', ''))
        message = result.get('message') or result.get('msg') or result.get('data') or '教务系统未提供说明'
        message = str(message)
        full_codes = {'52021132'}
        time_conflict_codes = {'52021133', '52021134', '52021135', '52021155'}
        busy_codes = {'52021142', '52021152'}
        system_limit_codes = {
            '52021100', '52021102', '52021103',
            *{'520211{:02d}'.format(number) for number in range(5, 32)},
            '52021136', '52021137', '52021138', '52021139', '52021140',
            '52021144', '52021150', '52021151', '52021153', '52021154',
            '52021156', '52021157', '52021158', '52021159', '52021160',
            '52021161', '52021162', '52021163', '52021164', '52021170',
            '52021203',
        }
        normalized = message.lower()
        if code in full_codes or any(word in normalized for word in ('人数已满', '名额已满', '剩余名额不足', '满员', 'student limit', 'vacancies')):
            return CourseSelectionFailure(code, message, '选课人数已满', True)
        if code in time_conflict_codes or any(word in normalized for word in ('时间冲突', '课程冲突', '考试冲突', 'schedule conflict', 'cross-campus')):
            return CourseSelectionFailure(code, message, '与已选课程时间冲突', False)
        if code in busy_codes or any(word in normalized for word in ('系统繁忙', '请稍后', 'busy')):
            return CourseSelectionFailure(code, message, '教务系统繁忙', True)
        if code in system_limit_codes or any(word in normalized for word in ('限制', '不允许', '超过', '未完成评教', '未注册', '欠费', '先修')):
            return CourseSelectionFailure(code, message, '系统选课限制', False)
        return CourseSelectionFailure(code, message, '教务系统拒绝选课', True)

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
                self.selection_stage = result.get('data') or {}
                self.semester_year = self.selection_stage.get('semesterYear')
        if not self.semester_year:
            raise CourseSelectorError('JWXT did not return an active course-selection term.')

    @property
    def sports_volunteer_enabled(self):
        """Whether JWXT currently accepts ranked PE volunteers.

        The official selection page enables PE volunteers only when the course
        selection type is active and the stage is 1 or 2 (pre-selection).
        """
        return (
            str(self.selection_stage.get('courseSelectType')) != '0'
            and self.is_preselection_stage
        )

    @property
    def is_preselection_stage(self):
        """Whether the current JWXT stage is a pre-selection/screening stage."""
        return str(self.selection_stage.get('electiveCourseStageCode')) in ('1', '2')

    @property
    def selection_stage_name(self):
        return self.selection_stage.get('electiveCourseStageName') or '未知阶段'

    def course_query(self, selected_type=1, selected_category=21):
        if not self.semester_year:
            raise CourseSelectorError('Log in before querying courses.')
        self.selected_type = int(selected_type)
        self.selected_category = int(selected_category)
        page_no = 1
        course_data = []
        while True:
            payload = {
                'pageNo': page_no,
                'pageSize': 100,
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
            data = result.get('data') or {}
            rows = data.get('rows') or []
            course_data.extend(rows)
            total = data.get('total')
            if not rows or total is None or len(course_data) >= int(total):
                break
            page_no += 1
        self.course_list = [
            {
                **item,
                '_selected_type': self.selected_type,
                '_selected_category': self.selected_category,
            }
            for item in course_data
        ]
        self.class_number_by_id.update({
            str(item.get('teachingClassId')): item.get('teachingClassNum', '')
            for item in course_data if item.get('teachingClassId')
        })
        return [{
            'cid': item.get('courseNum', ''),
            'cname': item.get('courseName', ''),
            'lecturer': (item.get('teachingTimePlace') or '').split(';')[0],
            'sid': item.get('teachingClassId', ''),
            'class_num': item.get('teachingClassNum', ''),
            'class_id': item.get('teachingClassId', ''),
            'snum': '{}/{}'.format(item.get('courseSelectedNum', 0), item.get('baseReceiveNum', 0)),
            'filter_selected_num': item.get('filterSelectedNum'),
            'status': item.get('selectedStatus') == 4 or item.get('selectedStatus') == '4',
            'selected_status': item.get('selectedStatus'),
        } for item in course_data]

    def course_query_categories(self, category_keys):
        """Query one or more configured categories and retain each class's selection metadata."""
        all_courses = []
        displayed_courses = []
        for category_key in category_keys:
            category_name, selected_type, selected_category = self.COURSE_CATEGORIES[category_key]
            courses = self.course_query(selected_type, selected_category)
            for course in courses:
                course['category_key'] = category_key
                course['category_name'] = category_name
            displayed_courses.extend(courses)
            all_courses.extend(self.course_list)
        self.course_list = all_courses
        return displayed_courses

    def selection_result_query(self, result_type, page_size=100):
        """Return courses in one of JWXT's selection-result states.

        ``success`` means the course is officially selected, ``failure`` means
        the selection did not succeed, and ``pending`` means it is waiting for
        the school's screening/lottery result.  These states are queried from
        JWXT's selected-course list, rather than inferred from a choose request.
        """
        filters = {
            'success': {
                'successStatus': '1', 'failureStatus': '0',
                'retiredClass': '0', 'waitingScreen': '0',
            },
            'failure': {
                'successStatus': '0', 'failureStatus': '1',
                'retiredClass': '0', 'waitingScreen': '0',
            },
            'pending': {
                'successStatus': '0', 'failureStatus': '0',
                'retiredClass': '0', 'waitingScreen': '1',
            },
        }
        if result_type not in filters:
            raise CourseSelectorError('Unknown selection result type: {}'.format(result_type))
        if not self.semester_year:
            raise CourseSelectorError('Log in before querying selection results.')

        page_no = 1
        courses = []
        while True:
            result = self.__request_json(
                self.selected_course_list_path,
                'Selection result query',
                {
                    'pageNo': page_no,
                    'pageSize': page_size,
                    'total': True,
                    'param': filters[result_type],
                },
            )
            data = result.get('data') or {}
            rows = data.get('rows') or []
            courses.extend(rows)
            total = data.get('total')
            if not rows or total is None or len(courses) >= int(total):
                break
            page_no += 1

        return [self.__format_selection_result(item, result_type) for item in courses]

    @staticmethod
    def __format_selection_result(item, result_type):
        """Normalize the fields returned by selectedCourse/list for callers."""
        return {
            'cid': item.get('courseNum', ''),
            'cname': item.get('courseName', ''),
            'class_num': item.get('teachingClassNum') or item.get('clazzNum', ''),
            'class_id': item.get('teachingClassId') or item.get('clazzId', ''),
            'course_id': item.get('courseId', ''),
            'selected_type': item.get('selectedType', ''),
            'lecturer': item.get('teacherName') or item.get('teachingTeacherName', ''),
            'schedule': item.get('teachingTimePlace', ''),
            'selected_num': item.get('selectCount', 0),
            'capacity': item.get('baseReceiveNum', 0),
            'status': item.get('status', ''),
            'result_type': result_type,
        }

    def selection_results_query(self):
        """Return all three official selection-result groups in one mapping."""
        return {
            result_type: self.selection_result_query(result_type)
            for result_type in ('success', 'failure', 'pending')
        }

    def drop_course(self, course_id, class_id, selected_type):
        """Drop an already selected or waiting-to-be-screened teaching class."""
        if not course_id or not class_id or selected_type in (None, ''):
            raise CourseSelectorError('缺少退课所需的课程 ID、教学班 ID 或选课类别。请先重新查询课程。')
        result = self.__request_json(
            self.course_back_path,
            'Course withdrawal',
            {
                'courseId': str(course_id),
                'clazzId': str(class_id),
                'selectedType': str(selected_type),
            },
        )
        return result.get('message') or result.get('data') or '退课请求已提交。'

    def sports_volunteer_query(self):
        """Return current PE volunteers in official preference order.

        ``studentFilterID`` is the identifier JWXT requires when saving a new
        order.  It is deliberately kept internal to this client; callers use
        the displayed teaching-class number or teaching-class ID instead.
        """
        if not self.sports_volunteer_enabled:
            raise CourseSelectorError('体育志愿仅能在预选阶段使用；当前为{}。'.format(self.selection_stage_name))
        result = self.__request_json(self.sports_volunteer_list_path, 'Sports volunteer query')
        rows = result.get('data') or []
        known_classes = dict(self.class_number_by_id)
        known_classes.update({
            str(item.get('teachingClassId')): item.get('teachingClassNum', '')
            for item in self.course_list if item.get('teachingClassId')
        })
        volunteers = []
        for item in rows:
            if not item.get('studentFilterID'):
                continue
            try:
                rank = int(item.get('volunteerNum') or item.get('sportVolunteer'))
            except (TypeError, ValueError):
                continue
            class_id = item.get('teachingClassId') or item.get('clazzId', '')
            volunteers.append({
                'class_num': item.get('teachingClassNum') or known_classes.get(str(class_id), ''),
                'class_id': class_id,
                'course_num': item.get('courseNum', ''),
                'course_name': item.get('courseName', ''),
                'schedule': item.get('teachingTimePlace', ''),
                'rank': rank,
                '_student_filter_id': item.get('studentFilterID'),
            })
        return sorted(volunteers, key=lambda item: item['rank'])

    def save_sports_volunteer_order(self, ordered_targets):
        """Save a complete, ordered list of up to four existing PE volunteers."""
        if not self.sports_volunteer_enabled:
            raise CourseSelectorError('当前不是体育预选阶段，不能设置志愿排序。')
        targets = [str(target).strip() for target in ordered_targets if str(target).strip()]
        if not 1 <= len(targets) <= 4 or len(set(targets)) != len(targets):
            raise CourseSelectorError('体育志愿须由 1 至 4 个不重复的教学班号或教学班 ID 组成。')
        volunteers = self.sports_volunteer_query()
        if len(volunteers) != len(targets):
            raise CourseSelectorError('请对当前全部 {} 个体育志愿排序后再保存。'.format(len(volunteers)))

        resolved = []
        for target in targets:
            matches = [
                item for item in volunteers
                if target in (str(item['class_num']), str(item['class_id']))
            ]
            if len(matches) != 1:
                raise CourseSelectorError('体育志愿中找不到教学班：{}'.format(target))
            resolved.append(matches[0])
        if len({item['_student_filter_id'] for item in resolved}) != len(resolved):
            raise CourseSelectorError('体育志愿排序中存在重复教学班。')

        payload = [
            {'studentFilterID': item['_student_filter_id'], 'volunteerNum': index}
            for index, item in enumerate(resolved, start=1)
        ]
        self.__request_json(self.sports_volunteer_update_path, 'Sports volunteer order update', payload)
        return [
            {key: value for key, value in item.items() if key != '_student_filter_id'}
            for item in resolved
        ]

    class course_select_thread(threading.Thread):
        def __init__(self, selector, select_id, selected_type, selected_category, course_label):
            super().__init__()
            self.selector = selector
            self.select_id = select_id
            self.selected_type = selected_type
            self.selected_category = selected_category
            self.course_label = course_label

        def run(self):
            self.selector.course_select(
                self.select_id, self.selected_type, self.selected_category, self.course_label
            )

    def course_select(self, select_id, selected_type, selected_category, course_label=''):
        attempt = 0
        while True:
            if self.selection_stop_event.is_set():
                self.__emit_event('stopped', course_label=course_label, class_id=str(select_id))
                return
            attempt += 1
            self.__emit_event(
                'attempt', course_label=course_label, class_id=str(select_id), attempt=attempt,
            )
            try:
                self.course_select_once(select_id, selected_type, selected_category)
            except CourseSelectionFailure as error:
                logging.warning('course selection rejected: %s', error)
                event = {
                    'course_label': course_label,
                    'class_id': str(select_id),
                    'attempt': attempt,
                    'message': error.message,
                    'reason': error.reason,
                    'code': error.code,
                    'retryable': error.retryable,
                }
                if not error.retryable:
                    self.__emit_event('failure', **event)
                    print('选课失败（{}）：{}；该原因不会自动重试。'.format(error.reason, error.message))
                    return
                self.__emit_event('retry', **event, delay=self.delay)
                print('暂未选上（{}）：{}；{} 秒后重试。'.format(error.reason, error.message, self.delay))
                if self.selection_stop_event.wait(self.delay):
                    self.__emit_event('stopped', course_label=course_label, class_id=str(select_id))
                    return
                continue
            except CourseSelectorError as error:
                logging.warning('course selection request failed: %s', error)
                self.__emit_event(
                    'retry', course_label=course_label, class_id=str(select_id), attempt=attempt,
                    message=str(error), reason='网络或服务异常', delay=self.delay,
                )
                if self.selection_stop_event.wait(self.delay):
                    self.__emit_event('stopped', course_label=course_label, class_id=str(select_id))
                    return
                continue
            print('Course selected (or already selected); stopping this request thread.')
            self.__emit_event(
                'success', course_label=course_label, class_id=str(select_id), attempt=attempt,
            )
            return

    def course_select_once(self, select_id, selected_type, selected_category):
        """Send one normal selection request without retrying it."""
        payload = {
            'clazzId': str(select_id),
            'selectedType': str(selected_type),
            'selectedCate': str(selected_category),
            'check': True,
        }
        return self.__request_json(
            self.course_select_path,
            'Course selection',
            payload,
            accepted_codes=(200, 52021104),
            error_factory=self.__selection_failure,
        )

    def course_select_wrapper(self, target_course_list_str):
        self.selection_stop_event.clear()
        targets = [target.strip() for target in target_course_list_str.split(',') if target.strip()]
        if not targets:
            raise CourseSelectorError('Enter at least one teaching class number or teaching class ID.')

        matching_courses = []
        missing = []
        ambiguous_course_numbers = []
        for target in targets:
            # JWXT's choose endpoint needs teachingClassId.  teachingClassNum is
            # the short, user-visible identifier; accept the internal ID too.
            matches = [
                item for item in self.course_list
                if str(item.get('teachingClassNum', '')) == target
                or str(item.get('teachingClassId', '')) == target
            ]
            if not matches:
                # Retain course-number input only when it identifies one class.
                course_number_matches = [
                    item for item in self.course_list if str(item.get('courseNum', '')) == target
                ]
                if len(course_number_matches) == 1:
                    matches = course_number_matches
                elif len(course_number_matches) > 1:
                    ambiguous_course_numbers.append(target)
                    continue
                else:
                    missing.append(target)
                    continue
            for item in matches:
                if item not in matching_courses:
                    matching_courses.append(item)

        messages = []
        if missing:
            messages.append('未在当前查询中找到：{}'.format(', '.join(sorted(missing))))
        if ambiguous_course_numbers:
            messages.append(
                '课程号存在多个教学班，不能直接选择：{}；请改填教学班号或教学班 ID'.format(
                    ', '.join(sorted(ambiguous_course_numbers))
                )
            )
        if messages:
            raise CourseSelectorError('；'.join(messages))

        sports_volunteers = [
            item for item in matching_courses
            if self.sports_volunteer_enabled and str(item.get('_selected_type')) == '3'
        ]
        normal_courses = [item for item in matching_courses if item not in sports_volunteers]
        summary = {'sports_volunteer_submitted': [], 'grab_started': []}

        if sports_volunteers:
            existing_volunteers = self.sports_volunteer_query()
            existing_class_ids = {str(item['class_id']) for item in existing_volunteers}
            new_volunteers = [
                item for item in sports_volunteers
                if str(item.get('teachingClassId')) not in existing_class_ids
            ]
            if len(existing_volunteers) + len(new_volunteers) > 4:
                raise CourseSelectorError(
                    '体育预选阶段最多保留 4 个志愿；当前已有 {} 个，新增后会超过上限。'.format(
                        len(existing_volunteers)
                    )
                )
            # Pre-selection is not a race: send each PE choice once.  The user
            # can then set its rank through save_sports_volunteer_order().
            for item in new_volunteers:
                if self.selection_stop_event.is_set():
                    self.__emit_event('stopped', course_label=self.__course_label(item))
                    break
                course_label = self.__course_label(item)
                self.__emit_event('sports_submitting', course_label=course_label)
                self.course_select_once(
                    item['teachingClassId'],
                    item.get('_selected_type', self.selected_type),
                    item.get('_selected_category', self.selected_category),
                )
                summary['sports_volunteer_submitted'].append(item.get('teachingClassNum', ''))
                self.__emit_event('sports_submitted', course_label=course_label)

        threads = []
        for item in normal_courses:
            course_label = self.__course_label(item)
            self.__emit_event('started', course_label=course_label)
            for _ in range(self.concurrent_request):
                thread = self.course_select_thread(
                    self,
                    item['teachingClassId'],
                    item.get('_selected_type', self.selected_type),
                    item.get('_selected_category', self.selected_category),
                    course_label,
                )
                thread.start()
                threads.append(thread)
            summary['grab_started'].append(item.get('teachingClassNum', ''))
        for thread in threads:
            thread.join()
        if self.selection_stop_event.is_set():
            self.__emit_event('stopped')
        return summary

    @staticmethod
    def __course_label(item):
        class_number = item.get('teachingClassNum') or item.get('teachingClassId', '')
        return '{}（教学班号：{}）'.format(item.get('courseName', ''), class_number)
