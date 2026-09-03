# 客服知识库 20 题「单一口径」评分报告

> 样本：6 个业务 sheet（运营平台/调度台/终端-安卓/终端-cat1/MDM/miniserver，培训知识库不参与）分层抽样 29 题；
> 生成的问题统一加 sheet 前缀（如「调度台-什么是下发消息」），携带模块上下文去问。
> 摸底口径：未接入客服库，直接用系统现有知识库作答——拒答反映知识库缺口，是补齐输入。

## 口径定义（单一口径）

**答案正确性 = 硬门槛 × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)**

- **硬门槛（0/1）**：系统给出实质作答且切题才为 1；缺口拒答（诚实说「未找到相关依据」）、反问澄清、答非所问 → 0（整题 0 分）。
- **关键事实命中率** = 甲方标准答案的「关键事实」中被系统回答命中的比例（核心结论/操作步骤/关键参数，漏了答案不成立）。
- **补充事实命中率** = 甲方标准答案的「补充事实」中被系统回答命中的比例（次要细节/举例/原因）。
- 关键/补充事实由评测模型对照甲方标准答案拆解；命中 = 语义等价，不要求逐字。

**单口径**：公式对所有被测题统一计算，拒答/澄清按 0 分计入（不藏数）；报告另给 实质作答/缺口拒答/需澄清 三类分解便于归因。

## 汇总

| 指标 | 值 |
| --- | --- |
| **答案正确性**（29 题单一口径，含拒答/澄清按 0 计） | **48.4%** |
| 实质作答 28 题 · 缺口拒答 1 题 · 需澄清 0 题 · 评测失败 0 题 | 分类归因 |

## 分域汇总

| 域 | 题数 | 答案正确性 | 实质作答 | 缺口拒答 | 需澄清 |
| --- | --- | --- | --- | --- | --- |
| miniserver | 29 | 48.4% | 28 | 1 | 0 |

## 每题明细（gate + 事实命中）

> 正确性 < 60% 标 ⚠（低分，需逐题归因）。

| # | 域 | 问题 | 硬门槛 | 关键事实(命中/总) | 补充事实(命中/总) | 答案正确性 |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | miniserver | miniserver-Miniserver数据保存多久 | 通过 | 0/1 | 0/0 | **20.0%**⚠ |
| 4 | miniserver | miniserver-miniserver支持的数据传输速率是 1gb… | 通过 | 1/1 | 0/0 | **100.0%** |
| 5 | miniserver | miniserver-Miniserver调度台定位上报时间不准确/谷… | 通过 | 0/1 | 0/0 | **20.0%**⚠ |
| 6 | miniserver | miniserver-Miniserver规格书 | 通过 | 1/1 | 0/0 | **100.0%** |
| 7 | miniserver | miniserver-miniserver网口最大的传输速度 | 通过 | 1/1 | 0/0 | **100.0%** |
| 8 | miniserver | miniserver-Minisever 上没有多媒体消息菜单 | 通过 | 1/1 | 0/1 | **80.0%** |
| 9 | miniserver | miniserver-Miniserver的3种使用方式 | 通过 | 3/16 | 0/3 | **15.0%**⚠ |
| 10 | miniserver | miniserver-固定IP的网络是否支持外网访问运营平台和调度台 | 通过 | 2/2 | 0/0 | **100.0%** |
| 11 | miniserver | miniserver-miniserver是否支持配置域名和证书 | 通过 | 1/3 | 0/1 | **26.7%**⚠ |
| 12 | miniserver | miniserver-新版本是否支持组织/部门管理 | 通过 | 0/2 | 0/0 | **20.0%**⚠ |
| 13 | miniserver | miniserver-Mini Server 是否支持多名调度员？ | 通过 | 1/1 | 0/1 | **80.0%** |
| 14 | miniserver | miniserver-Mini Server 是否支持下载单个用户的录… | 通过 | 2/2 | 0/0 | **100.0%** |
| 15 | miniserver | miniserver-miniserver支持监听吗? | 通过 | 1/1 | 0/0 | **100.0%** |
| 15 | miniserver | miniserver-miniserver支持监听吗? | 通过 | 1/1 | 0/0 | **100.0%** |
| 16 | miniserver | miniserver-Miniserver的OS（操作系统）是什么 | 通过 | 0/1 | 1/1 | **20.0%**⚠ |
| 16 | miniserver | miniserver-Miniserver的OS（操作系统）是什么 | 通过 | 1/1 | 0/0 | **100.0%** |
| 17 | miniserver | miniserver-关于MiniServer（微型服务器）相关能力与… | 通过 | 0/6 | 0/0 | **20.0%**⚠ |
| 18 | miniserver | miniserver-miniserver 云容灾 | 通过 | 0/3 | 0/2 | **0.0%**⚠ |
| 19 | miniserver | miniserver-MINISEVER 新版 录音记录/定位保存多久 | 通过 | 0/2 | 0/0 | **20.0%**⚠ |
| 20 | miniserver | miniserver-imei在公网激活的，要先删除再激活吗？ | 不通过 | 0/2 | 0/0 | **0.0%**⚠ |
| 21 | miniserver | miniserver-公司版miniserver可以添加imei吗？ | 通过 | 1/1 | 0/0 | **100.0%** |
| 22 | miniserver | miniserver-调度员如何连接到miniserver | 通过 | 1/2 | 0/1 | **40.0%**⚠ |
| 23 | miniserver | miniserver-miniserver更换logo/运营平台/调度台 | 通过 | 0/1 | 0/1 | **0.0%**⚠ |
| 24 | miniserver | miniserver-miniserver的终端apk是否支持录音记录… | 通过 | 0/1 | 0/1 | **0.0%**⚠ |
| 25 | miniserver | miniserver-miniserver如何开启AES256加密功能 | 通过 | 2/2 | 1/2 | **90.0%** |
| 26 | miniserver | miniserver-miniserver升级后 重新添加账号提示超出… | 通过 | 0/2 | 0/1 | **0.0%**⚠ |
| 27 | miniserver | miniserver-cat1机器智能收到按麦提示音，没有语音 | 通过 | 0/2 | 0/0 | **20.0%**⚠ |
| 28 | miniserver | miniserver-如果创建好的账号提示账号不存在排查思路 | 通过 | 2/5 | 0/1 | **32.0%**⚠ |
| 29 | miniserver | miniserver-miniserver重置网络 | 通过 | 0/3 | 0/1 | **0.0%**⚠ |

## 低分清单（答案正确性 < 60%，附未命中的关键事实）

| # | 域 | 问题 | 答案正确性 | 未命中的关键事实 |
| --- | --- | --- | --- | --- |
| 18 | miniserver | miniserver-miniserver 云容灾 | 0.0% | 当前为手动切换，需在MiniServer管理平台操作；前期由运维监控异常并告警后手动点击切换；后续再研究异常判… |
| 20 | miniserver | miniserver-imei在公网激活的，要先删除再激活吗？ | 0.0% | 公网激活的imei需先删除；再激活到新版Miniserver下使用 |
| 23 | miniserver | miniserver-miniserver更换logo/运营平… | 0.0% | 更换logo属于商机问题，应转销售处理 |
| 24 | miniserver | miniserver-miniserver的终端apk是否支持… | 0.0% | 录音一直都有 |
| 26 | miniserver | miniserver-miniserver升级后 重新添加账号… | 0.0% | 联系邹波报客户名字查询记录；需要在管理平台上删除重建 |
| 29 | miniserver | miniserver-miniserver重置网络 | 0.0% | 重置网络应恢复到代理模式自动获取IP；代理模式自动获取IP换网线自动检测，失败则重启；代理模式固定IP换网线需… |
| 9 | miniserver | miniserver-Miniserver的3种使用方式 | 15.0% | 局域网: 设置本机内网IP，公网IP空置；局域网: 终端配置内网IP和context登录；局域网: 调度台和运… |
| 2 | miniserver | miniserver-Miniserver数据保存多久 | 20.0% | 语音记录保存3个月 |
| 16 | miniserver | miniserver-Miniserver的OS（操作系统）是… | 20.0% | Debian 9 |
| 5 | miniserver | miniserver-Miniserver调度台定位上报时间不… | 20.0% | 更新网盘解决对应问题的ROM |
| 12 | miniserver | miniserver-新版本是否支持组织/部门管理 | 20.0% | 已发布版本不支持组织/部门管理；下一个版本才支持组织/部门管理 |
| 17 | miniserver | miniserver-关于MiniServer（微型服务器）相… | 20.0% | 存量MiniServer不支持升级到最新版本；新设备升级需镜像方式且具备镜像恢复能力；用户账号创建后不支持修改… |
| 19 | miniserver | miniserver-MINISEVER 新版 录音记录/定位… | 20.0% | 录音记录保存6个月；定位记录保存3个月 |
| 27 | miniserver | miniserver-cat1机器智能收到按麦提示音，没有语音 | 20.0% | 未检查是否开启AES256功能；未说明cat1不支持语音加密功能 |
| 11 | miniserver | miniserver-miniserver是否支持配置域名和证书 | 26.7% | 未说明已自动添加SSL证书，反而指导手动安装；未说明无需额外申请域名和SSL证书 |
| 28 | miniserver | miniserver-如果创建好的账号提示账号不存在排查思路 | 32.0% | 检查配置的什么网络；代理模式下自测能否登录；自测登录不了时切换内网再切回 |
| 22 | miniserver | miniserver-调度员如何连接到miniserver | 40.0% | 连接方式与端口无实际关系 |

## 缺口拒答题（诚实拒答，gate 不通过按 0 计）

> 这些题的答案就在甲方客服库的「解决方法」列、但不在系统现有知识库——接入客服库即可覆盖。

| # | 域 | 问题 | 甲方标准答案 |
| --- | --- | --- | --- |
| 20 | miniserver | miniserver-imei在公网激活的，要先删除再激活吗？ | imei在公网激活的，需要先删除再激活到新版Miniserver下使用 |

## 说明与边界

- **答案正确性依赖「标准答案」质量**：甲方的「解决方法」是人工客服口径；关键/补充事实由评测模型拆解，属自动化近似，非甲方标注。
- 单口径含缺口拒答按 0 计：主口径低分由「未接入客服库」主导，是摸底值；接入后缺口题转为可作答。
- judge 模型：deepseek:deepseek-v4-flash。
