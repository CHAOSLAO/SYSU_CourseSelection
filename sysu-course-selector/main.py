from scs import CourseSelectorError, course_selector
from info import name, pwd

def main():
    try:
        cs = course_selector()
        cs.pre_login()
        cs.in_login(name, pwd)
        print('Query range: 1=专业选修, 2=公共必修, 3=体育')
        selected_categories = input('Enter ranges, separated by English commas (default: 1): ').strip() or '1'
        category_keys = [key.strip() for key in selected_categories.split(',') if key.strip()]
        invalid_keys = set(category_keys) - set(cs.COURSE_CATEGORIES)
        if invalid_keys:
            raise CourseSelectorError('Unknown query range: {}'.format(', '.join(sorted(invalid_keys))))
        course_data = cs.course_query_categories(category_keys)
        print('{:10}{:30}{:10}{:10}{:10}'.format('Course ID', 'Course Name', 'Lecturer', 'Selected/All', 'Chosen'))
        for cd in course_data:
            print('{:10}{:30}{:10}{:10}{:10}'.format(cd['cid'], cd['cname'], cd['lecturer'], cd['snum'], cd['status']))
        target_course_list_str = input('Enter Course ID, separated by English commas: ')
        cs.course_select_wrapper(target_course_list_str)
    except CourseSelectorError as error:
        print('Unable to continue: {}'.format(error))

if __name__ == '__main__':
    main()
