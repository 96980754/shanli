# 企业用户 Excel 一键导入（2026-07-27）

## 目标

在现有用户管理页面提供 Excel 模板下载、全量校验预览和原子批量创建能力，用于企业内部用户首次开户，并支持将企业微信 UserID 直接作为系统 UID。

## 验收标准

- [x] 仅接受 `.xlsx`，单次最多 2 MiB、200 行。
- [x] Excel 固定字段：`username`、`uid`、`initial_password`、`phone_number`、`role`、`department_name`。
- [x] `role` 可留空，默认 `user`；仅允许 `user/admin`，禁止 `superadmin`。
- [x] 任一行错误、文件内重复或与现有用户冲突时，整批不写入。
- [x] 部门管理员只能向自己的部门导入普通用户；超级管理员可向已有部门导入普通用户或管理员。
- [x] 初始密码仅在请求内存中使用，入库前使用 Argon2 哈希，不在预览和日志中返回。
- [x] 导入时显式 UID 保留大小写，企业微信用户可精确填写企微 UserID。
- [x] 导入成功后刷新用户列表并记录一条汇总操作日志。

## Excel 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| username | 是 | 展示名称，2-20 个字符。 |
| uid | 是 | 登录 ID / 外部身份 ID，导入后不可修改。 |
| initial_password | 是 | 8-128 个字符的初始密码。 |
| phone_number | 否 | 中国大陆手机号。 |
| role | 否 | 留空默认 user；可填 user 或 admin。 |
| department_name | 条件必填 | 超级管理员导入时必填；部门管理员固定为自己的部门。 |
