"""Tkinter GUI for querying and selecting SYSU courses.

Passwords entered here are used only for the current session and are never
written to disk.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from scs import CourseSelectorError, course_selector


RESULT_TABS = (
    ('success', '已选成功'),
    ('failure', '已选失败'),
    ('pending', '已选待筛选'),
)


class CourseSelectorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SYSU 选课助手')
        self.minsize(980, 700)
        self.geometry('1120x800')
        self.selector = None
        self.course_references = {}
        self.status_text = tk.StringVar(value='请输入 NetID 和密码后登录。')
        self.stage_text = tk.StringVar(value='选课阶段将在登录后显示。')
        self.volunteer_text = tk.StringVar(value='登录后可查看体育志愿状态。')
        self.volunteers = []
        self.course_tables = {}
        self.monitor_window = None
        self.monitor_text = None
        self.monitor_events = []
        self.successful_class_ids = set()
        self.concurrent_var = tk.StringVar(value='1')
        self.delay_var = tk.StringVar(value='5')
        self.timeout_var = tk.StringVar(value='5')
        self.proxy_enabled_var = tk.BooleanVar(value=False)
        self.proxy_port_var = tk.StringVar(value='1080')
        self.category_vars = {
            key: tk.BooleanVar(value=True)
            for key in course_selector.COURSE_CATEGORIES
        }
        self.result_tables = {}
        self._build_interface()

    def _build_interface(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)

        login = ttk.LabelFrame(outer, text='登录（不会保存密码）', padding=10)
        login.pack(fill=tk.X)
        ttk.Label(login, text='NetID').grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self.username_entry = ttk.Entry(login, width=22)
        self.username_entry.grid(row=0, column=1, sticky=tk.W)
        ttk.Label(login, text='密码').grid(row=0, column=2, sticky=tk.W, padx=(16, 6))
        self.password_entry = ttk.Entry(login, width=26, show='●')
        self.password_entry.grid(row=0, column=3, sticky=tk.W)
        self.login_button = ttk.Button(login, text='登录', command=self.login)
        self.login_button.grid(row=0, column=4, sticky=tk.W, padx=(16, 0))
        ttk.Label(login, text='并发请求').grid(row=1, column=0, sticky=tk.W, pady=(10, 0))
        ttk.Spinbox(login, from_=1, to=10, width=6, textvariable=self.concurrent_var).grid(
            row=1, column=1, sticky=tk.W, pady=(10, 0)
        )
        ttk.Label(login, text='重试间隔（秒）').grid(row=1, column=2, sticky=tk.W, pady=(10, 0))
        ttk.Spinbox(login, from_=1, to=60, width=6, textvariable=self.delay_var).grid(
            row=1, column=3, sticky=tk.W, pady=(10, 0)
        )
        ttk.Label(login, text='网络超时（秒）').grid(row=1, column=4, sticky=tk.W, pady=(10, 0), padx=(16, 4))
        ttk.Spinbox(login, from_=2, to=60, width=6, textvariable=self.timeout_var).grid(
            row=1, column=5, sticky=tk.W, pady=(10, 0)
        )
        ttk.Checkbutton(login, text='使用本机 SOCKS5 代理', variable=self.proxy_enabled_var).grid(
            row=1, column=6, sticky=tk.W, pady=(10, 0), padx=(16, 4)
        )
        ttk.Entry(login, width=7, textvariable=self.proxy_port_var).grid(
            row=1, column=7, sticky=tk.W, pady=(10, 0)
        )
        ttk.Label(login, text='端口').grid(row=1, column=8, sticky=tk.W, pady=(10, 0), padx=(4, 0))
        login.columnconfigure(9, weight=1)

        actions = ttk.LabelFrame(outer, text='课程查询与选课', padding=10)
        actions.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(actions, text='查询范围：').grid(row=0, column=0, sticky=tk.W)
        for offset, (key, (label, _, _)) in enumerate(course_selector.COURSE_CATEGORIES.items(), start=1):
            ttk.Checkbutton(actions, text=label, variable=self.category_vars[key]).grid(
                row=0, column=offset, sticky=tk.W, padx=(0, 12)
            )
        ttk.Button(actions, text='查询可选课程', command=self.query_courses).grid(
            row=0, column=4, sticky=tk.E, padx=(8, 0)
        )
        ttk.Button(actions, text='打开抢课监视窗口', command=self.open_monitor).grid(
            row=0, column=5, sticky=tk.E, padx=(8, 0)
        )
        ttk.Label(actions, text='目标教学班号／教学班 ID（多个用英文逗号分隔）：').grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(10, 0)
        )
        self.target_entry = ttk.Entry(actions)
        self.target_entry.grid(row=1, column=2, columnspan=2, sticky=tk.EW, pady=(10, 0), padx=(0, 8))
        ttk.Button(actions, text='开始选课', command=self.select_courses).grid(
            row=1, column=4, sticky=tk.E, pady=(10, 0)
        )
        ttk.Label(actions, textvariable=self.stage_text, foreground='#1f5f99').grid(
            row=2, column=0, columnspan=5, sticky=tk.W, pady=(10, 0)
        )
        actions.columnconfigure(3, weight=1)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        course_page = ttk.Frame(notebook, padding=6)
        notebook.add(course_page, text='可选课程')
        course_actions = ttk.Frame(course_page)
        course_actions.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(course_actions, text='选中教学班后可直接加入目标列表（体育课请按教学班号区分）：').pack(side=tk.LEFT)
        ttk.Button(course_actions, text='加入选中教学班', command=self.add_selected_classes).pack(side=tk.RIGHT)
        course_tabs = ttk.Notebook(course_page)
        course_tabs.pack(fill=tk.BOTH, expand=True)
        for category_key, (category_name, _, _) in course_selector.COURSE_CATEGORIES.items():
            tab = ttk.Frame(course_tabs, padding=4)
            course_tabs.add(tab, text=category_name)
            self.course_tables[category_key] = self._make_table(
                tab,
                ('课程号', '教学班号', '课程名称', '教师 / 时间地点', '已选 / 容量', '待筛选人数', '当前状态'),
                (100, 115, 210, 320, 110, 105, 95),
                selectmode='extended',
            )

        volunteer_page = ttk.Frame(notebook, padding=6)
        notebook.add(volunteer_page, text='体育志愿（仅预选阶段）')
        ttk.Label(volunteer_page, textvariable=self.volunteer_text, anchor=tk.W).pack(fill=tk.X, pady=(0, 6))
        volunteer_actions = ttk.Frame(volunteer_page)
        volunteer_actions.pack(fill=tk.X, pady=(0, 6))
        self.refresh_volunteers_button = ttk.Button(
            volunteer_actions, text='刷新体育志愿', command=self.refresh_volunteers
        )
        self.refresh_volunteers_button.pack(side=tk.LEFT)
        self.volunteer_up_button = ttk.Button(
            volunteer_actions, text='上移志愿', command=lambda: self.move_volunteer(-1)
        )
        self.volunteer_up_button.pack(side=tk.LEFT, padx=(8, 0))
        self.volunteer_down_button = ttk.Button(
            volunteer_actions, text='下移志愿', command=lambda: self.move_volunteer(1)
        )
        self.volunteer_down_button.pack(side=tk.LEFT, padx=(8, 0))
        self.save_volunteers_button = ttk.Button(
            volunteer_actions, text='保存志愿排序', command=self.save_volunteer_order
        )
        self.save_volunteers_button.pack(side=tk.RIGHT)
        self.volunteer_table = self._make_table(
            volunteer_page,
            ('志愿', '教学班号／教学班 ID', '课程', '上课时间地点'),
            (70, 200, 260, 500),
        )

        results_page = ttk.Frame(notebook, padding=6)
        notebook.add(results_page, text='选课结果')
        ttk.Button(results_page, text='刷新选课结果', command=self.refresh_results).pack(anchor=tk.E, pady=(0, 6))
        result_notebook = ttk.Notebook(results_page)
        result_notebook.pack(fill=tk.BOTH, expand=True)
        for result_type, title in RESULT_TABS:
            tab = ttk.Frame(result_notebook, padding=4)
            result_notebook.add(tab, text=title)
            self.result_tables[result_type] = self._make_table(
                tab,
                ('课程号', '课程名称', '教学班', '教师', '已选 / 容量', '上课时间地点'),
                (120, 220, 100, 120, 110, 410),
            )

        ttk.Label(outer, textvariable=self.status_text, anchor=tk.W).pack(fill=tk.X, pady=(10, 0))

    @staticmethod
    def _make_table(parent, headings, widths, selectmode='browse'):
        container = ttk.Frame(parent)
        container.pack(fill=tk.BOTH, expand=True)
        columns = tuple('c{}'.format(index) for index in range(len(headings)))
        table = ttk.Treeview(container, columns=columns, show='headings', selectmode=selectmode)
        for column, heading, width in zip(columns, headings, widths):
            table.heading(column, text=heading)
            table.column(column, width=width, minwidth=80, anchor=tk.W, stretch=True)
        y_scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=table.yview)
        x_scrollbar = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=table.xview)
        table.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)
        table.grid(row=0, column=0, sticky=tk.NSEW)
        y_scrollbar.grid(row=0, column=1, sticky=tk.NS)
        x_scrollbar.grid(row=1, column=0, sticky=tk.EW)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        return table

    def _run_in_background(self, action, on_success, description):
        def worker():
            try:
                value = action()
            except (CourseSelectorError, OSError, ValueError) as error:
                self.after(0, lambda: self._show_error(description, error))
            except Exception as error:  # keep a GUI callback failure from closing the app
                self.after(0, lambda: self._show_error(description, error))
            else:
                self.after(0, lambda: on_success(value))
        threading.Thread(target=worker, daemon=True).start()

    def _require_login(self):
        if self.selector is None:
            messagebox.showwarning('尚未登录', '请先完成登录。', parent=self)
            return False
        return True

    def _show_error(self, description, error):
        self.status_text.set('{}失败：{}'.format(description, error))
        messagebox.showerror('{}失败'.format(description), str(error), parent=self)

    def login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        if not username or not password:
            messagebox.showwarning('信息不完整', '请输入 NetID 和密码。', parent=self)
            return
        try:
            settings = self._read_runtime_settings()
        except ValueError as error:
            self._show_error('运行参数检查', error)
            return
        self.login_button.configure(state=tk.DISABLED)
        self.status_text.set('正在登录并初始化教务会话…')

        def action():
            selector = course_selector(**settings)
            selector.pre_login()
            selector.in_login(username, password)
            return selector

        def done(selector):
            self.selector = selector
            selector.event_callback = self._on_selector_event
            self.password_entry.delete(0, tk.END)
            self.login_button.configure(state=tk.NORMAL)
            self._update_stage_ui()
            self.status_text.set('登录成功，当前选课学期：{}，{}。'.format(
                selector.semester_year, selector.selection_stage_name,
            ))
            if selector.sports_volunteer_enabled:
                self.refresh_volunteers()

        def failed(description, error):
            self.login_button.configure(state=tk.NORMAL)
            self._show_error(description, error)

        def worker():
            try:
                selector = action()
            except Exception as error:
                self.after(0, lambda: failed('登录', error))
            else:
                self.after(0, lambda: done(selector))
        threading.Thread(target=worker, daemon=True).start()

    def _update_stage_ui(self):
        if self.selector.sports_volunteer_enabled:
            self.stage_text.set(
                '当前为{}：体育课使用预选志愿，最多 4 个；提交后请在“体育志愿”页调整 1–4 志愿。'.format(
                    self.selector.selection_stage_name
                )
            )
            self.volunteer_text.set('体育志愿可排序。请先选课，再在此确认并保存第一至第四志愿。')
            state = tk.NORMAL
        else:
            self.stage_text.set(
                '当前为{}：抢选阶段，不使用体育志愿；“开始选课”会持续尝试普通选课。'.format(
                    self.selector.selection_stage_name
                )
            )
            self.volunteer_text.set('当前不是体育预选阶段，志愿功能不可用。')
            state = tk.DISABLED
        for button in (
            self.refresh_volunteers_button, self.volunteer_up_button,
            self.volunteer_down_button, self.save_volunteers_button,
        ):
            button.configure(state=state)

    def open_monitor(self):
        if self.monitor_window is not None and self.monitor_window.winfo_exists():
            self.monitor_window.deiconify()
            self.monitor_window.lift()
            self.monitor_window.focus_force()
            return
        window = tk.Toplevel(self)
        window.title('自动抢课运行监视')
        window.geometry('760x430')
        window.minsize(560, 280)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text='这里显示每个教学班的开始尝试、重试、成功和体育预选提交情况。').pack(
            anchor=tk.W, pady=(0, 6)
        )
        text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED, height=16)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.monitor_window = window
        self.monitor_text = text
        self._render_monitor()
        window.protocol('WM_DELETE_WINDOW', self._close_monitor)

    def _close_monitor(self):
        if self.monitor_window is not None:
            self.monitor_window.destroy()
        self.monitor_window = None
        self.monitor_text = None

    def _on_selector_event(self, event):
        """Receive selection-thread events and transfer them to Tk's UI thread."""
        self.after(0, lambda event=dict(event): self._append_monitor_event(event))

    def _append_monitor_event(self, event):
        event_type = event['type']
        label = event.get('course_label') or '教学班 {}'.format(event.get('class_id', ''))
        if event_type == 'started':
            message = '已启动持续抢选：{}'.format(label)
        elif event_type == 'attempt':
            message = '正在尝试：{}（第 {} 次）'.format(label, event['attempt'])
        elif event_type == 'retry':
            message = '未成功，{} 秒后重试：{}；原因：{}'.format(
                event['delay'], label, event.get('message', '未知错误'),
            )
        elif event_type == 'success':
            message = '选课成功（或已选）：{}'.format(label)
        elif event_type == 'sports_submitting':
            message = '正在提交体育预选志愿：{}'.format(label)
        elif event_type == 'sports_submitted':
            message = '已提交体育预选志愿：{}；请在“体育志愿”页保存排序。'.format(label)
        else:
            message = '{}：{}'.format(event_type, label)
        line = '[{}] {}'.format(time.strftime('%H:%M:%S'), message)
        self.monitor_events.append(line)
        self.monitor_events = self.monitor_events[-500:]
        self._render_monitor()

        if event_type == 'success' and event.get('class_id') not in self.successful_class_ids:
            self.successful_class_ids.add(event.get('class_id'))
            self.status_text.set(message)
            self.bell()
            messagebox.showinfo('选课成功', '{}\n请刷新“选课结果”确认最终状态。'.format(message), parent=self)

    def _render_monitor(self):
        if self.monitor_text is None or not self.monitor_text.winfo_exists():
            return
        self.monitor_text.configure(state=tk.NORMAL)
        self.monitor_text.delete('1.0', tk.END)
        self.monitor_text.insert(tk.END, '\n'.join(self.monitor_events) or '尚未开始自动抢课。')
        self.monitor_text.see(tk.END)
        self.monitor_text.configure(state=tk.DISABLED)

    def _read_runtime_settings(self):
        """Read the advanced controls without persisting any of their values."""
        try:
            settings = {
                'concurrent_request': int(self.concurrent_var.get()),
                'delay': int(self.delay_var.get()),
                'timeout': int(self.timeout_var.get()),
                'use_socks5_proxy': self.proxy_enabled_var.get(),
                'socks5_proxy_port': int(self.proxy_port_var.get()),
            }
        except ValueError as error:
            raise ValueError('并发数、重试间隔、网络超时和代理端口必须是整数。') from error
        if not 1 <= settings['concurrent_request'] <= 10:
            raise ValueError('并发请求数应在 1 到 10 之间。')
        if not 1 <= settings['delay'] <= 60:
            raise ValueError('重试间隔应在 1 到 60 秒之间。')
        if not 2 <= settings['timeout'] <= 60:
            raise ValueError('网络超时应在 2 到 60 秒之间。')
        if settings['use_socks5_proxy'] and not 1 <= settings['socks5_proxy_port'] <= 65535:
            raise ValueError('SOCKS5 代理端口应在 1 到 65535 之间。')
        return settings

    def query_courses(self):
        if not self._require_login():
            return
        categories = [key for key, var in self.category_vars.items() if var.get()]
        if not categories:
            messagebox.showwarning('未选择范围', '请至少选择一个课程范围。', parent=self)
            return
        self.status_text.set('正在查询可选课程…')
        self._run_in_background(
            lambda: self.selector.course_query_categories(categories),
            self._show_courses,
            '查询课程',
        )

    def _show_courses(self, courses):
        for table in self.course_tables.values():
            table.delete(*table.get_children())
        self.course_references = {}
        for index, course in enumerate(courses):
            category_key = course.get('category_key', '1')
            table = self.course_tables[category_key]
            item_id = 'course-{}-{}'.format(category_key, index)
            self.course_references[item_id] = course['class_num'] or course['class_id']
            table.insert('', tk.END, iid=item_id, values=(
                course['cid'], course['class_num'] or '—', course['cname'], course['lecturer'],
                course['snum'],
                course['filter_selected_num']
                if self.selector.is_preselection_stage and course['filter_selected_num'] is not None else '—',
                self._course_status_label(course['selected_status']),
            ))
        category_counts = {
            key: len([course for course in courses if course.get('category_key') == key])
            for key in self.course_tables
        }
        self.status_text.set('已查询到 {} 个教学班（{}）。'.format(
            len(courses),
            '，'.join(
                '{} {} 个'.format(course_selector.COURSE_CATEGORIES[key][0], count)
                for key, count in category_counts.items() if count
            ) or '无结果',
        ))

    def add_selected_classes(self):
        selected = [
            item_id for table in self.course_tables.values() for item_id in table.selection()
        ]
        if not selected:
            messagebox.showwarning('未选择教学班', '请在“可选课程”列表中选择一个或多个教学班。', parent=self)
            return
        references = [self.course_references[item_id] for item_id in selected if self.course_references.get(item_id)]
        current = [part.strip() for part in self.target_entry.get().split(',') if part.strip()]
        for reference in references:
            if reference not in current:
                current.append(reference)
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, ','.join(current))
        self.status_text.set('已加入 {} 个教学班号；确认后点击“开始选课”。'.format(len(references)))

    @staticmethod
    def _course_status_label(status):
        labels = {
            4: '已选成功', '4': '已选成功',
            3: '待筛选', '3': '待筛选',
            1: '已停开', '1': '已停开',
        }
        return labels.get(status, '未选')

    def select_courses(self):
        if not self._require_login():
            return
        targets = self.target_entry.get().strip()
        if not targets:
            messagebox.showwarning('未填写教学班', '请填写要选的教学班号或教学班 ID。', parent=self)
            return
        if not self.selector.course_list:
            messagebox.showwarning('请先查询', '请先查询课程，再从结果中填写教学班号或教学班 ID。', parent=self)
            return
        self.open_monitor()
        if self.selector.sports_volunteer_enabled:
            self.status_text.set('正在提交预选体育志愿（最多 4 个）或持续尝试普通课程；体育志愿提交后请调整排序。')
        else:
            self.status_text.set('正在持续尝试选课；可随时刷新“选课结果”确认最终状态。')
        self._run_in_background(
            lambda: self.selector.course_select_wrapper(targets),
            self._selection_finished,
            '选课',
        )

    def _selection_finished(self, summary):
        sports = summary.get('sports_volunteer_submitted', [])
        grabbing = summary.get('grab_started', [])
        messages = []
        if sports:
            messages.append('已提交 {} 个体育志愿；请到“体育志愿”页确认并保存排序'.format(len(sports)))
            self.refresh_volunteers()
        if grabbing:
            messages.append('普通课程选课请求已完成，请刷新“选课结果”确认最终状态')
        self.status_text.set('；'.join(messages) or '所选教学班已存在，无需重复提交。')

    def refresh_volunteers(self):
        if not self._require_login():
            return
        if not self.selector.sports_volunteer_enabled:
            self.status_text.set('当前不是体育预选阶段，不能查询或排序体育志愿。')
            return
        self.status_text.set('正在查询体育志愿…')

        def action():
            # On first login there is no course table yet.  Querying PE once
            # gives the volunteer list a readable teaching-class number.
            if not self.selector.course_list:
                self.selector.course_query(3, 10)
            return self.selector.sports_volunteer_query()

        self._run_in_background(action, self._show_volunteers, '查询体育志愿')

    def _show_volunteers(self, volunteers):
        self.volunteers = list(volunteers)
        self.volunteer_table.delete(*self.volunteer_table.get_children())
        for index, volunteer in enumerate(self.volunteers):
            self.volunteer_table.insert('', tk.END, iid='volunteer-{}'.format(index), values=(
                '第 {} 志愿'.format(index + 1),
                volunteer['class_num'] or volunteer['class_id'],
                '{} - {}'.format(volunteer['course_num'], volunteer['course_name']),
                volunteer['schedule'],
            ))
        self.volunteer_text.set('当前已设置 {} 个体育志愿；可选中一条后上移或下移，再保存排序。'.format(len(volunteers)))
        self.status_text.set('体育志愿已更新。')

    def move_volunteer(self, direction):
        if not self.volunteers:
            messagebox.showwarning('没有体育志愿', '请先刷新体育志愿列表。', parent=self)
            return
        selected = self.volunteer_table.selection()
        if len(selected) != 1:
            messagebox.showwarning('请选择一项', '请选择一个体育志愿后再移动。', parent=self)
            return
        index = int(selected[0].rsplit('-', 1)[1])
        new_index = index + direction
        if not 0 <= new_index < len(self.volunteers):
            return
        self.volunteers[index], self.volunteers[new_index] = self.volunteers[new_index], self.volunteers[index]
        self._show_volunteers(self.volunteers)
        self.volunteer_table.selection_set('volunteer-{}'.format(new_index))

    def save_volunteer_order(self):
        if not self._require_login() or not self.selector.sports_volunteer_enabled:
            return
        if not self.volunteers:
            messagebox.showwarning('没有体育志愿', '请先刷新体育志愿列表。', parent=self)
            return
        if not messagebox.askyesno('保存体育志愿排序', '将按当前顺序保存第一至第四志愿，是否继续？', parent=self):
            return
        targets = [item['class_num'] or item['class_id'] for item in self.volunteers]
        self.status_text.set('正在保存体育志愿排序…')
        self._run_in_background(
            lambda: self.selector.save_sports_volunteer_order(targets),
            self._show_volunteers,
            '保存体育志愿排序',
        )

    def refresh_results(self):
        if not self._require_login():
            return
        self.status_text.set('正在查询已选成功、已选失败和待筛选课程…')
        self._run_in_background(
            self.selector.selection_results_query,
            self._show_results,
            '查询选课结果',
        )

    def _show_results(self, results):
        for result_type, _ in RESULT_TABS:
            self._replace_rows(self.result_tables[result_type], [
                (
                    course['cid'], course['cname'], course['class_num'], course['lecturer'],
                    '{}/{}'.format(course['selected_num'], course['capacity']), course['schedule'],
                )
                for course in results[result_type]
            ])
        self.status_text.set(
            '选课结果已更新：成功 {} 门，失败 {} 门，待筛选 {} 门。'.format(
                len(results['success']), len(results['failure']), len(results['pending']),
            )
        )

    @staticmethod
    def _replace_rows(table, rows):
        table.delete(*table.get_children())
        for row in rows:
            table.insert('', tk.END, values=row)


def launch():
    app = CourseSelectorApp()
    app.mainloop()
