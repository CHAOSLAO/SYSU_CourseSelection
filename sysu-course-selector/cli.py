"""The original command-line interface, kept for terminal users."""

from info import name, pwd

from scs import CourseSelectorError, course_selector


def print_sports_volunteers(selector):
    volunteers = selector.sports_volunteer_query()
    if not volunteers:
        print('当前没有体育志愿。')
        return volunteers
    print('当前体育志愿（按第一至第四志愿排序）：')
    print('{:8}{:18}{:12}{:30}'.format('志愿', '教学班号/ID', '课程号', '课程名称'))
    for volunteer in volunteers:
        print('{:8}{:18}{:12}{:30}'.format(
            '第{}志愿'.format(volunteer['rank']),
            volunteer['class_num'] or volunteer['class_id'],
            volunteer['course_num'], volunteer['course_name'],
        ))
    return volunteers


def print_courses(selector, course_data):
    for category_key, (category_name, _, _) in selector.COURSE_CATEGORIES.items():
        courses = [course for course in course_data if course.get('category_key') == category_key]
        print('\n{}（{} 个教学班）'.format(category_name, len(courses)))
        if selector.is_preselection_stage:
            print('{:10}{:12}{:30}{:10}{:10}{:12}{:10}'.format(
                '课程号', '教学班号', '课程名称', '教师', '已选/容量', '待筛选人数', '已选',
            ))
        else:
            print('{:10}{:12}{:30}{:10}{:10}{:10}'.format(
                '课程号', '教学班号', '课程名称', '教师', '已选/容量', '已选',
            ))
        for course in courses:
            if selector.is_preselection_stage:
                print('{:10}{:12}{:30}{:10}{:10}{:12}{:10}'.format(
                    course['cid'], course['class_num'], course['cname'], course['lecturer'], course['snum'],
                    course['filter_selected_num'] if course['filter_selected_num'] is not None else '—', course['status'],
                ))
            else:
                print('{:10}{:12}{:30}{:10}{:10}{:10}'.format(
                    course['cid'], course['class_num'], course['cname'], course['lecturer'], course['snum'], course['status'],
                ))


def main():
    selector = None
    try:
        selector = course_selector()
        selector.pre_login()
        selector.in_login(name, pwd)
        print('当前选课阶段：{}'.format(selector.selection_stage_name))
        if selector.sports_volunteer_enabled:
            print('体育处于预选阶段：最多 4 个志愿，可在选课后设置第一至第四志愿。')
        else:
            print('当前为抢选/非预选阶段：体育课程不使用志愿排序，将按普通选课方式持续尝试。')
        print('正在获取专业选修、公共必修和体育的可选课程清单…')
        course_data = selector.course_query_categories(list(selector.COURSE_CATEGORIES))
        print_courses(selector, course_data)
        targets = input('输入教学班号或教学班 ID，多个用英文逗号分隔（直接回车跳过选课）：').strip()
        if targets:
            summary = selector.course_select_wrapper(targets)
            if summary['sports_volunteer_submitted']:
                print('已提交体育预选志愿，请确认并设置排序。')

        if selector.sports_volunteer_enabled:
            volunteers = print_sports_volunteers(selector)
            if volunteers:
                order = input('输入新的志愿顺序（从第一志愿起，教学班号/ID用英文逗号分隔；直接回车不修改）：').strip()
                if order:
                    saved = selector.save_sports_volunteer_order(order.split(','))
                    print('已保存 {} 个体育志愿的排序。'.format(len(saved)))
    except CourseSelectorError as error:
        print('无法继续：{}'.format(error))
    except KeyboardInterrupt:
        if selector is not None:
            selector.stop_course_selection()
        print('\n已发送停止选课指令；当前网络请求结束后将退出。')


if __name__ == '__main__':
    main()
