"""Tkinter GUI for querying and selecting SYSU courses.

Passwords entered here are used only for the current session and are never
written to disk.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from scs import CASVerificationRequired, CourseSelectorError, course_selector


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
        self.login_diagnostic_text = tk.StringVar(value='登录诊断：尚未开始。')
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
        self.timeout_var = tk.StringVar(value='15')
        self.proxy_mode_var = tk.StringVar(value='系统代理')
        self.proxy_port_var = tk.StringVar(value='7897')
        self.stage_mode_var = tk.StringVar(value='')
        self._last_login_username = None
        self._last_login_password = None
        self._verification_selector = None
        self._verification_stage_mode = None
        self.category_vars = {
            key: tk.BooleanVar(value=True)
            for key in course_selector.COURSE_CATEGORIES
        }
        self.result_tables = {}
        self.result_references = {}
        self._build_interface()
        self.protocol('WM_DELETE_WINDOW', self.exit_app)

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
        self.relogin_button = ttk.Button(login, text='重新登录（当前账号）', command=self.relogin)
        self.relogin_button.grid(row=0, column=5, sticky=tk.W, padx=(8, 0))
        self.switch_account_button = ttk.Button(login, text='切换账号', command=self.switch_account)
        self.switch_account_button.grid(row=0, column=6, sticky=tk.W, padx=(8, 0))
        self.exit_button = ttk.Button(login, text='退出', command=self.exit_app)
        self.exit_button.grid(row=0, column=7, sticky=tk.W, padx=(8, 0))
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
        ttk.Combobox(
            login,
            width=12,
            state='readonly',
            textvariable=self.proxy_mode_var,
            values=('系统代理', 'HTTP 代理', 'SOCKS5 代理', '不使用代理'),
        ).grid(row=1, column=6, sticky=tk.W, pady=(10, 0), padx=(16, 4))
        ttk.Entry(login, width=7, textvariable=self.proxy_port_var).grid(
            row=1, column=7, sticky=tk.W, pady=(10, 0)
        )
        ttk.Label(login, text='端口（手动代理）').grid(row=1, column=8, sticky=tk.W, pady=(10, 0), padx=(4, 0))
        ttk.Label(login, text='当前选课阶段（登录前必选）').grid(
            row=2, column=0, sticky=tk.W, pady=(10, 0)
        )
        self.preselection_mode_button = ttk.Radiobutton(
            login, text='预选阶段（体育四志愿）', variable=self.stage_mode_var, value='preselection',
            command=self._preview_stage_mode,
        )
        self.preselection_mode_button.grid(row=2, column=1, columnspan=2, sticky=tk.W, pady=(10, 0))
        self.grab_mode_button = ttk.Radiobutton(
            login, text='抢选阶段（体育自动换课）', variable=self.stage_mode_var, value='grab',
            command=self._preview_stage_mode,
        )
        self.grab_mode_button.grid(row=2, column=3, columnspan=2, sticky=tk.W, pady=(10, 0))
        ttk.Label(login, text='登录后会与教务当前阶段核对，只显示对应功能。', foreground='#666666').grid(
            row=2, column=5, columnspan=4, sticky=tk.W, pady=(10, 0)
        )
        self.verification_frame = ttk.LabelFrame(login, text='CAS 人工协助验证', padding=8)
        self.verification_frame.grid(row=3, column=0, columnspan=9, sticky=tk.EW, pady=(10, 0))
        self.verification_text = tk.StringVar(value='')
        ttk.Label(self.verification_frame, textvariable=self.verification_text, foreground='#9b3b00').pack(
            side=tk.LEFT, padx=(0, 12)
        )
        self.verification_code_entry = ttk.Entry(self.verification_frame, width=16, show='●')
        self.verification_code_entry.pack(side=tk.LEFT)
        self.verification_send_button = ttk.Button(
            self.verification_frame, text='发送企微验证码', command=self.request_work_wechat_code,
        )
        self.verification_send_button.pack(side=tk.LEFT, padx=(8, 0))
        self.verification_continue_button = ttk.Button(
            self.verification_frame, text='验证并继续登录', command=self.complete_work_wechat_verification,
        )
        self.verification_continue_button.pack(side=tk.LEFT, padx=(8, 0))
        self.verification_cancel_button = ttk.Button(
            self.verification_frame, text='取消本次登录', command=self.cancel_human_verification,
        )
        self.verification_cancel_button.pack(side=tk.LEFT, padx=(8, 0))
        self.verification_frame.grid_remove()
        ttk.Label(login, textvariable=self.login_diagnostic_text, foreground='#666666', wraplength=1000).grid(
            row=4, column=0, columnspan=9, sticky=tk.W, pady=(8, 0)
        )
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
        ttk.Button(actions, text='停止选课', command=self.stop_selection).grid(
            row=1, column=5, sticky=tk.E, padx=(8, 0), pady=(10, 0)
        )
        ttk.Label(
            actions,
            text='无需先扫描课程；未知教学班号会自动解析。仅有教学班 ID 时可填：ID@选课类型@选课类别（如 ID@3@10）。',
            foreground='#666666',
        ).grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(8, 0))
        self.sports_swap_note = ttk.Label(
            actions,
            text='体育抢选阶段：若已选体育不在目标教学班范围内，检测到目标空位后将自动退当前体育并立即抢选目标。',
            foreground='#9b3b00',
        )
        self.sports_swap_note.grid(row=3, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))
        ttk.Label(actions, textvariable=self.stage_text, foreground='#1f5f99').grid(
            row=4, column=0, columnspan=5, sticky=tk.W, pady=(10, 0)
        )
        self.sports_swap_note.grid_remove()
        actions.columnconfigure(3, weight=1)

        notebook = ttk.Notebook(outer)
        self.main_notebook = notebook
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
        self.volunteer_page = volunteer_page
        notebook.add(volunteer_page, text='体育志愿（仅预选阶段）')
        notebook.hide(volunteer_page)
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
        result_actions = ttk.Frame(results_page)
        result_actions.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(result_actions, text='刷新选课结果', command=self.refresh_results).pack(side=tk.RIGHT)
        ttk.Button(result_actions, text='退选当前选中课程', command=self.drop_selected_course).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
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

        force_page = ttk.Frame(notebook, padding=12)
        notebook.add(force_page, text='强制选课')
        ttk.Label(
            force_page,
            text='教学班 ID 查找笔记',
            font=('Microsoft YaHei UI', 13, 'bold'),
        ).pack(anchor=tk.W, pady=(0, 10))
        force_note = tk.Text(
            force_page,
            wrap=tk.WORD,
            relief=tk.FLAT,
            padx=12,
            pady=12,
            font=('Microsoft YaHei UI', 10),
            spacing1=4,
            spacing3=8,
        )
        force_scrollbar = ttk.Scrollbar(force_page, orient=tk.VERTICAL, command=force_note.yview)
        force_note.configure(yscrollcommand=force_scrollbar.set)
        force_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        force_note.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        force_note.insert(tk.END, (
            '一、如何找到教学班 ID\n\n'
            '个人可选课程接口只返回当前账号有资格看到的教学班，因此无法从该列表直接找到高年级或其他范围的课程。'
            '可改用教务系统已有的“全校开课查询”只读接口：\n\n'
            'schedule/agg/schoolOpeningCoursesSchedule/querySchoolOpeningCourses\n\n'
            '用学期、课程号和教学班号精确查询。例如：学期 2026-1、课程号 EIT413、教学班号 202613751。'
            '返回记录中的 class_ID 对应选课接口使用的 teachingClassId / clazzId。\n\n'
            '为了确认字段含义，可以选择一门同时出现在个人可选列表和全校开课查询中的课程进行对照。'
            '通信原理 EIT320（教学班号 202613696）在个人选课接口中的 teachingClassId 与全校开课接口中的 '
            'class_ID 均为 2074439187608080385，证明两个字段指向同一个教学班。\n\n'
            '二、教学班 ID 的构成特点\n\n'
            '1. 教学班 ID 是教务系统生成的长十进制内部标识，不能由教学班号直接换算。\n'
            '2. class_ID 才是教学班 ID；sumClassesID 是汇总班 ID，两者用途不同。\n'
            '3. 同一教学班的 class_ID 与 sumClassesID 数值可能非常接近，但不能据此推算或替换。\n'
            '4. 课程 ID、教学班号、汇总班 ID 和教学班 ID 是四种不同标识，选课接口需要 clazzId。\n\n'
            '三、结论\n\n'
            '这个方法可以合法、只读地找到教学班 ID，但仅凭 ID 不能实现“强制选课”。选课请求仍会由教务服务器校验年级、培养方案、'
            '选课范围、时间冲突、容量和选课阶段。若要改变这些资格限制，必须由学校教务端授权或管理员调整权限；客户端不能也不应自行提权。\n\n'
            '本栏目仅作为开发笔记，不提供绕过权限或强制选课功能。'
        ))
        force_note.configure(state=tk.DISABLED)

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
                self.after(0, lambda error=error: self._show_error(description, error))
            except Exception as error:  # keep a GUI callback failure from closing the app
                self.after(0, lambda error=error: self._show_error(description, error))
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

    def _show_login_diagnostics(self, selector):
        """Display safe CAS/JWXT checkpoints without exposing account or cookie data."""
        markers = tuple(getattr(selector, 'login_diagnostics', ()) or ())
        self.login_diagnostic_text.set(
            '登录诊断：{}'.format(' → '.join(markers) if markers else '未取得诊断标志。')
        )

    def _clear_session(self, clear_stage=False):
        """Stop the active session and clear data shown from that session."""
        if self.selector is not None:
            self.selector.stop_course_selection()
        self.selector = None
        self.course_references = {}
        self.result_references = {}
        self.volunteers = []
        self.successful_class_ids.clear()
        self.monitor_events = []
        self.target_entry.delete(0, tk.END)
        self.password_entry.delete(0, tk.END)
        for table in self.course_tables.values():
            table.delete(*table.get_children())
        for table in self.result_tables.values():
            table.delete(*table.get_children())
        self.volunteer_table.delete(*self.volunteer_table.get_children())
        self._clear_human_verification()
        if clear_stage:
            self.stage_mode_var.set('')
            self.preselection_mode_button.configure(state=tk.NORMAL)
            self.grab_mode_button.configure(state=tk.NORMAL)
        self._preview_stage_mode()

    def _clear_human_verification(self):
        """Forget a pending MFA session without displaying its private state."""
        self._verification_selector = None
        self._verification_stage_mode = None
        self.verification_code_entry.delete(0, tk.END)
        self.verification_text.set('')
        self.verification_frame.grid_remove()

    def cancel_human_verification(self):
        self._clear_human_verification()
        self.login_button.configure(state=tk.NORMAL)
        self.status_text.set('已取消本次 CAS 验证；可重新登录。')

    def _show_human_verification(self, selector, stage_mode, error):
        self._verification_selector = selector
        self._verification_stage_mode = stage_mode
        self._show_login_diagnostics(selector)
        methods = set(error.methods)
        self.verification_frame.grid()
        if 'webWorkWechatMsgAuth' in methods:
            self.verification_text.set('CAS 需要二次验证。推荐使用企业微信验证码：先发送，再输入你在企微中收到的验证码。')
            self.verification_send_button.configure(state=tk.NORMAL)
            self.verification_continue_button.configure(state=tk.NORMAL)
        else:
            self.verification_text.set('CAS 需要二次验证（{}）。请在官方 CAS 页面完成后重新登录。'.format(
                '、'.join(course_selector.CAS_HUMAN_METHODS.get(method, method) for method in error.methods) or '无可自动处理方式'
            ))
            self.verification_send_button.configure(state=tk.DISABLED)
            self.verification_continue_button.configure(state=tk.DISABLED)
        self.login_button.configure(state=tk.NORMAL)
        self.status_text.set('已保留本次 CAS 临时会话，等待你完成二次验证。')

    def request_work_wechat_code(self):
        selector = self._verification_selector
        if selector is None:
            messagebox.showwarning('没有验证会话', '请先登录并等待 CAS 要求二次验证。', parent=self)
            return
        self.verification_send_button.configure(state=tk.DISABLED)
        self.status_text.set('正在向已绑定的企业微信申请验证码…')

        def done(_result):
            self.verification_send_button.configure(state=tk.NORMAL)
            self.verification_code_entry.focus_set()
            self.status_text.set('企业微信验证码已申请。请查看企微并输入验证码，然后点击“验证并继续登录”。')

        def failed(description, error):
            self.verification_send_button.configure(state=tk.NORMAL)
            self._show_error(description, error)

        def worker():
            try:
                result = selector.begin_human_verification()
            except Exception as error:
                self.after(0, lambda error=error: failed('申请企业微信验证码', error))
            else:
                self.after(0, lambda: done(result))
        threading.Thread(target=worker, daemon=True).start()

    def complete_work_wechat_verification(self):
        selector = self._verification_selector
        if selector is None:
            messagebox.showwarning('没有验证会话', '请先登录并等待 CAS 要求二次验证。', parent=self)
            return
        code = self.verification_code_entry.get().strip()
        if not code:
            messagebox.showwarning('缺少验证码', '请输入企业微信收到的验证码。', parent=self)
            return
        self.verification_continue_button.configure(state=tk.DISABLED)
        self.status_text.set('正在向 CAS 验证验证码并继续建立教务会话…')

        def done(_result):
            stage_mode = self._verification_stage_mode
            self._clear_human_verification()
            try:
                selector.set_selection_mode(stage_mode)
            except CourseSelectorError as error:
                self._show_error('选课阶段核对', error)
                return
            self._login_succeeded(selector)

        def failed(description, error):
            self.verification_continue_button.configure(state=tk.NORMAL)
            self._show_error(description, error)

        def worker():
            try:
                selector.complete_human_verification(code)
            except Exception as error:
                self.after(0, lambda error=error: failed('CAS 二次验证', error))
            else:
                self.after(0, lambda: done(None))
        threading.Thread(target=worker, daemon=True).start()

    def relogin(self):
        """Log in again immediately with this process's most recent credentials."""
        if not self._last_login_username or not self._last_login_password:
            messagebox.showwarning('没有可用会话', '请先手动完成一次登录。', parent=self)
            return
        stage_mode = self.stage_mode_var.get()
        self._clear_session(clear_stage=False)
        self.status_text.set('正在使用当前账号重新登录并建立新会话…')
        self.login(self._last_login_username, self._last_login_password, stage_mode)

    def switch_account(self):
        """Return to the login form so a different account can be entered."""
        self._clear_session(clear_stage=True)
        self._last_login_username = None
        self._last_login_password = None
        self.password_entry.delete(0, tk.END)
        self.stage_text.set('请选择选课阶段并输入新账号登录。')
        self.volunteer_text.set('登录后可查看体育志愿状态。')
        self.status_text.set('当前会话已清除；请输入新账号和密码。')
        self.username_entry.focus_set()

    def exit_app(self):
        """Stop local selection loops before closing the GUI window."""
        if self.selector is not None and not messagebox.askyesno(
            '确认退出', '退出前将停止当前自动选课进程，是否继续？', parent=self
        ):
            return
        if self.selector is not None:
            self.selector.stop_course_selection()
        self._last_login_username = None
        self._last_login_password = None
        self.destroy()

    def login(self, username=None, password=None, stage_mode=None):
        username = self.username_entry.get().strip() if username is None else username
        password = self.password_entry.get() if password is None else password
        if not username or not password:
            messagebox.showwarning('信息不完整', '请输入 NetID 和密码。', parent=self)
            return
        stage_mode = self.stage_mode_var.get() if stage_mode is None else stage_mode
        if not stage_mode:
            messagebox.showwarning('请选择阶段', '请先选择当前是“预选阶段”还是“抢选阶段”。', parent=self)
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
            selector_holder['selector'] = selector
            try:
                selector.pre_login()
                selector.in_login(username, password)
                selector.set_selection_mode(stage_mode)
            except CASVerificationRequired as error:
                return selector, error
            return selector, None

        def failed(description, error, selector=None):
            self.login_button.configure(state=tk.NORMAL)
            if self.selector is None:
                self.preselection_mode_button.configure(state=tk.NORMAL)
                self.grab_mode_button.configure(state=tk.NORMAL)
            if selector is not None:
                self._show_login_diagnostics(selector)
            self._show_error(description, error)

        selector_holder = {}

        def worker():
            try:
                selector, verification_error = action()
            except Exception as error:
                selector = selector_holder.get('selector')
                self.after(0, lambda error=error, selector=selector: failed('登录', error, selector))
            else:
                if verification_error is not None:
                    self.after(0, lambda: self._show_human_verification(selector, stage_mode, verification_error))
                else:
                    self.after(0, lambda: self._login_succeeded(selector))
        threading.Thread(target=worker, daemon=True).start()

    def _login_succeeded(self, selector):
        """Finish the common GUI setup after password-only or MFA login."""
        self.selector = selector
        selector.event_callback = self._on_selector_event
        self._last_login_username = self.username_entry.get().strip() or self._last_login_username
        self._last_login_password = self.password_entry.get() or self._last_login_password
        self.password_entry.delete(0, tk.END)
        self.login_button.configure(state=tk.NORMAL)
        self.preselection_mode_button.configure(state=tk.DISABLED)
        self.grab_mode_button.configure(state=tk.DISABLED)
        self._show_login_diagnostics(selector)
        self._update_stage_ui()
        self.status_text.set('登录成功，当前选课学期：{}，{}。'.format(
            selector.semester_year, selector.selection_stage_name,
        ))
        self._run_in_background(
            lambda: selector.course_query_categories(list(selector.COURSE_CATEGORIES)),
            self._initial_courses_ready,
            '登录后获取可选课程',
        )

    def _initial_courses_ready(self, courses):
        self._show_courses(courses)
        if self.selector.sports_volunteer_enabled:
            self.refresh_volunteers()

    def _preview_stage_mode(self):
        """Show only the stage-specific UI selected before login."""
        if self.stage_mode_var.get() == 'preselection':
            self.main_notebook.add(self.volunteer_page, text='体育志愿（仅预选阶段）')
            self.sports_swap_note.grid_remove()
            self.stage_text.set('已选择预选阶段：登录后将显示体育四志愿功能并与教务阶段核对。')
        elif self.stage_mode_var.get() == 'grab':
            self.main_notebook.hide(self.volunteer_page)
            self.sports_swap_note.grid()
            self.stage_text.set('已选择抢选阶段：登录后将显示体育自动换课功能并与教务阶段核对。')
        else:
            self.main_notebook.hide(self.volunteer_page)
            self.sports_swap_note.grid_remove()

    def _update_stage_ui(self):
        if self.selector.is_preselection_stage:
            self.stage_text.set(
                '当前为{}：体育课使用预选志愿，最多 4 个；提交后请在“体育志愿”页调整 1–4 志愿。'.format(
                    self.selector.selection_stage_name
                )
            )
            if self.selector.sports_volunteer_enabled:
                self.volunteer_text.set('体育志愿可排序。请先选课，再在此确认并保存第一至第四志愿。')
                state = tk.NORMAL
            else:
                self.volunteer_text.set('教务当前未开放体育志愿提交，志愿功能暂不可用。')
                state = tk.DISABLED
            self.main_notebook.add(self.volunteer_page, text='体育志愿（仅预选阶段）')
            self.sports_swap_note.grid_remove()
        else:
            self.stage_text.set(
                '当前为{}：抢选阶段，不使用体育志愿；“开始选课”会持续尝试普通选课。'.format(
                    self.selector.selection_stage_name
                )
            )
            self.volunteer_text.set('当前不是体育预选阶段，志愿功能不可用。')
            state = tk.DISABLED
            self.main_notebook.hide(self.volunteer_page)
            self.sports_swap_note.grid()
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
        if event_type == 'stopping':
            message = '已收到停止选课指令；当前请求结束后不会再重试。'
        elif event_type == 'stopped':
            message = '已停止自动选课{}'.format('：{}'.format(label) if event.get('course_label') else '')
        elif event_type == 'started':
            message = '已启动持续抢选：{}'.format(label)
        elif event_type == 'attempt':
            message = '正在尝试：{}（第 {} 次）'.format(label, event['attempt'])
        elif event_type == 'retry':
            message = '暂未选上，{} 秒后重试：{}；原因：{}（{}）'.format(
                event['delay'], label, event.get('reason', '未知原因'), event.get('message', '未知错误'),
            )
        elif event_type == 'failure':
            message = '选课失败，不再重试：{}；原因：{}（{}）'.format(
                label, event.get('reason', '未知原因'), event.get('message', '未知错误'),
            )
        elif event_type == 'success':
            message = '选课成功（或已选）：{}'.format(label)
        elif event_type == 'sports_target_satisfied':
            message = '当前已选体育课已在目标范围内，结束体育抢选：{}'.format(label)
        elif event_type == 'sports_waiting_for_vacancy':
            message = '目标体育课暂无空位，{} 秒后重新扫描：{}'.format(event['delay'], label)
        elif event_type == 'sports_swap_ready':
            message = '检测到目标体育课空位：{}；正在退掉当前体育课：{}'.format(
                label, event.get('current_course_label', ''),
            )
        elif event_type == 'sports_dropped':
            message = '已退掉体育课：{}；正在抢选目标：{}'.format(
                label, event.get('target_course_label', ''),
            )
        elif event_type == 'sports_grabbing_target':
            message = '正在抢选有空位的目标体育课：{}'.format(label)
        elif event_type == 'sports_swap_retry':
            message = '体育换课退课请求未完成，{} 秒后重试：{}（{}）'.format(
                event['delay'], label, event.get('message', '未知错误'),
            )
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

        if event_type == 'failure':
            self.status_text.set(message)
            messagebox.showwarning('选课失败', message, parent=self)
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
        proxy_modes = {
            '系统代理': 'system',
            'HTTP 代理': 'http',
            'SOCKS5 代理': 'socks5',
            '不使用代理': 'none',
        }
        try:
            settings = {
                'concurrent_request': int(self.concurrent_var.get()),
                'delay': int(self.delay_var.get()),
                'timeout': int(self.timeout_var.get()),
                'proxy_mode': proxy_modes[self.proxy_mode_var.get()],
                'proxy_host': '127.0.0.1',
                'proxy_port': int(self.proxy_port_var.get()),
            }
        except (KeyError, ValueError) as error:
            raise ValueError('并发数、重试间隔、网络超时和代理端口必须是整数。') from error
        if not 1 <= settings['concurrent_request'] <= 10:
            raise ValueError('并发请求数应在 1 到 10 之间。')
        if not 1 <= settings['delay'] <= 60:
            raise ValueError('重试间隔应在 1 到 60 秒之间。')
        if not 2 <= settings['timeout'] <= 60:
            raise ValueError('网络超时应在 2 到 60 秒之间。')
        if settings['proxy_mode'] in ('http', 'socks5') and not 1 <= settings['proxy_port'] <= 65535:
            raise ValueError('代理端口应在 1 到 65535 之间。')
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

    def stop_selection(self):
        if not self._require_login():
            return
        self.selector.stop_course_selection()
        self.open_monitor()
        self.status_text.set('已发送停止选课指令；当前网络请求完成后将停止重试。')

    def _selection_finished(self, summary):
        sports = summary.get('sports_volunteer_submitted', [])
        grabbing = summary.get('grab_started', [])
        messages = []
        if summary.get('sports_target_satisfied'):
            messages.append('当前已选体育课已在目标范围内，未发起退课或抢选')
        if summary.get('sports_swapped_from'):
            messages.append('已退原体育课并开始抢选目标体育课，请刷新“选课结果”确认最终状态')
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
        self.result_references = {}
        for result_type, _ in RESULT_TABS:
            table = self.result_tables[result_type]
            table.delete(*table.get_children())
            for index, course in enumerate(results[result_type]):
                item_id = 'result-{}-{}'.format(result_type, index)
                self.result_references[item_id] = course
                table.insert('', tk.END, iid=item_id, values=(
                    course['cid'], course['cname'], course['class_num'], course['lecturer'],
                    '{}/{}'.format(course['selected_num'], course['capacity']), course['schedule'],
                ))
        self.status_text.set(
            '选课结果已更新：成功 {} 门，失败 {} 门，待筛选 {} 门。'.format(
                len(results['success']), len(results['failure']), len(results['pending']),
            )
        )

    def drop_selected_course(self):
        if not self._require_login():
            return
        selected = [
            item_id for table in self.result_tables.values() for item_id in table.selection()
        ]
        if len(selected) != 1:
            messagebox.showwarning('请选择一门课程', '请在“已选成功”或“已选待筛选”列表中选择一门课程。', parent=self)
            return
        course = self.result_references.get(selected[0])
        if course is None or course['result_type'] == 'failure':
            messagebox.showwarning('不能退选', '只能退选已选成功或待筛选课程。', parent=self)
            return
        course_id = course['course_id']
        class_id = course['class_id']
        selected_type = course['selected_type']
        if not course_id or not selected_type:
            matched = next((
                item for item in self.selector.course_list
                if str(item.get('teachingClassId')) == str(class_id)
            ), None)
            if matched:
                course_id = course_id or matched.get('courseId')
                selected_type = selected_type or matched.get('_selected_type')
        if not messagebox.askyesno(
            '确认退选',
            '确定退选“{}”（教学班号：{}）吗？'.format(course['cname'], course['class_num'] or class_id),
            parent=self,
        ):
            return
        self.status_text.set('正在提交退课请求…')
        self._run_in_background(
            lambda: self.selector.drop_course(course_id, class_id, selected_type),
            self._drop_finished,
            '退课',
        )

    def _drop_finished(self, message):
        self.status_text.set('退课成功：{}'.format(message))
        messagebox.showinfo('退课成功', str(message), parent=self)
        self.refresh_results()

    @staticmethod
    def _replace_rows(table, rows):
        table.delete(*table.get_children())
        for row in rows:
            table.insert('', tk.END, values=row)


def launch():
    app = CourseSelectorApp()
    app.mainloop()
