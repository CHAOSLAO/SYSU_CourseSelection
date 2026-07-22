# SYSU Course Selector

面向开发与维护的中大 JWXT 选课辅助工具。提供 Tkinter GUI 与 CLI，支持专业选修、公共必修、体育课查询、选课/退课、结果查询和体育志愿/换课逻辑。

> 仅按用户操作调用学校接口；资格、容量、冲突和培养方案始终由 JWXT 判定。不得绕过 CAS 或选课限制。

## 运行

```bash
pip install -r requirements.txt
python main.py                 # GUI
python cli.py                  # CLI，凭据来自本地 info.py
```

Windows 可双击 `run.bat`。`info.py` 仅保留占位符并忽略本地真实凭据。

## 认证与会话

核心实现位于 `scs.py`，每个 `course_selector` 实例持有独立的内存 CookieJar；不读取、写入或共享浏览器 Cookie。

```text
GET  /esc-sso/api/v3/auth/policy       获取 RSA 公钥
POST /esc-sso/api/v3/auth/doLogin      密码登录（RSA 加密）
GET  /esc-sso/login?service=JWXT_SSO   建立 JWXT SSO
```

JWXT SSO 目标为：

```text
/jwxt/api/sso/cas/login?pattern=student-login
```

登录后通过学生信息、选课信息等接口初始化当前学期与选课阶段。

### CAS MFA：企业微信验证码

CAS 将风险验证重定向到 `mfaLogin` 时，程序保留同一 CAS 临时会话，并按官方前端逻辑处理：

```text
GET  /esc-sso/api/v3/auth/queryAllValid
GET  /esc-sso/api/v3/webWorkWechatMsgAuth/send?username=<NetID>
POST /esc-sso/app/upgradelogin
```

最后一步的请求体包含 `authType=webWorkWechatMsgAuth`、`username`、`msgCode`，以及 MFA 配置中的 `appId`、`appUrl`。它不是普通 `doLogin`；使用普通登录端点会被 CAS 拒绝为“当前登录方式未开启”。当前实测验证码为 4 位，但代码以服务端 `codeLength` 为准。

人工边界：程序只申请并提交用户输入的验证码，不读取企微内容、不代替确认。CAS 仅提供企业微信网页登录/其他方式时，应在官方页面完成验证。

## JWXT 接口与课程标识

主要路径：

```text
choose-course-front-server/classCourseInfo/course/list
choose-course-front-server/classCourseInfo/course/choose
choose-course-front-server/classCourseInfo/course/back
choose-course-front-server/selectedCourse/list
choose-course-front-server/selectedCourse/sportsSelectedlist
choose-course-front-server/selectedCourse/updateSportsSelectedlist
schedule/agg/schoolOpeningCoursesSchedule/querySchoolOpeningCourses
```

选课提交使用内部 `teachingClassId`；教学班号（`teachingClassNum` / `clazzNum`）只用于展示与解析。课程号不唯一，体育课必须按教学班号/班级/教师时间区分。

`querySchoolOpeningCourses` 的 `class_ID` 已与已知课程的 `teachingClassId` 对比一致，可用于定位不在个人列表中的公开教学班；它不是 `courseId` 或 `sumClassesID`，也不赋予范围外选课资格。

手动目标格式：

```text
教学班号
内部教学班ID@选课类型@选课类别
```

类别映射：

| 类别 | selectedType | selectedCate |
| --- | ---: | ---: |
| 专业选修 | 1 | 21 |
| 公共必修 | 1 | 10 |
| 体育 | 3 | 10 |

## 阶段与体育逻辑

启动时选择阶段，登录后使用 `electiveCourseStageCode` 校验：`1/2` 为预选，其余为抢选。

- 预选：体育课最多 4 个志愿，按志愿顺序提交；显示待筛选人数。
- 抢选：无志愿。若已选体育不在目标范围，监视目标余量；有空位时先退原体育课，再尝试选择目标班。该操作不保留原课。

## GUI / CLI 设计要点

- GUI 登录成功后自动获取三类可选课程；监视窗口接收后台重试、成功、失败、待筛选和体育换课事件。
- 登录失败原因区分满员（重试）、时间冲突/系统限制（停止无效重试）和系统繁忙（重试）。
- “重新登录”仅复用本进程内存中的账号密码；切换账号与退出会清理会话。
- CLI 保持同一认证、阶段和体育逻辑；CAS MFA 时提示发送并输入企业微信验证码。

## 更新摘要

### 2026-07-22

- 接入并实测 CAS 企业微信验证码 MFA；修正为 `app/upgradelogin` 专用通道。
- GUI/CLI 增加人工验证码续登；启动器改为 Windows CRLF 批处理。

### 2026-07-20

- 增加阶段化 GUI/CLI、体育自动换课、结果/退课/监视功能和教学班 ID 解析。

## 致谢与许可证

功能改编自 [Siriussee/sysu-course-selector](https://github.com/Siriussee/sysu-course-selector)，接口参考 [SYSU-Tang/Sysuer](https://github.com/SYSU-Tang/Sysuer)。本仓库许可证为 [Apache License 2.0](LICENSE)。
