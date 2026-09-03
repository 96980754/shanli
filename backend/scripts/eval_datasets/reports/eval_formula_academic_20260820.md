# Agent 客服知识库问答「答案正确性」评估公式（学术版）

> 数据：合并 372 题（客服库摸底 269 题 + MCX/定位产品新表 103 题）
> 评测模型 $\mathcal{J}$：deepseek:deepseek-v4-flash（judge LLM）
> 汇总值：答案正确性 $= 66.0\%$（实质作答口径）

---

## 1. 记号（Notation）

设数据集含 $N$ 道题。第 $i$ 道题为三元组 $(Q_i, G_i, \hat{A}_i)$：

| 记号 | 含义 |
|---|---|
| $Q_i \in \mathcal{Q}$ | 提问（带分区前缀，如「MCX-…」） |
| $G_i \in \mathcal{G}$ | 甲方标准答案（gold answer），评分基准 |
| $\hat{A}_i \in \mathcal{A}$ | 系统回答（agent answer） |
| $\mathcal{J}$ | 评测模型（judge LLM） |

## 2. 事实拆解（Fact Decomposition）

评测模型将标准答案 $G_i$ 分解为两个**互斥**的事实点集合：

$$D(G_i) = (K_i,\; S_i), \qquad K_i \cap S_i = \varnothing,\;\; K_i \cup S_i = \mathrm{Facts}(G_i)$$

- **关键事实** $K_i = \{k_{i,1}, \dots, k_{i,\kappa_i}\}$，基数 $\kappa_i = |K_i|$：缺任一则答案不成立的核心结论 / 操作步骤 / 关键参数；
- **补充事实** $S_i = \{s_{i,1}, \dots, s_{i,\sigma_i}\}$，基数 $\sigma_i = |S_i|$：次要细节 / 举例 / 原因。

## 3. 事实命中判定（Fact Coverage）

对任一事实点 $f \in \mathrm{Facts}(G_i)$，定义**覆盖指示函数**：

$$\delta_i(f) = \mathbb{1}\big[\; f \text{ 被 } \hat{A}_i \text{ 语义覆盖}\;\big] \in \{0, 1\}$$

语义覆盖 = 语义等价即算覆盖（不要求逐字一致）；明确矛盾或缺失视为未覆盖。$\delta_i$ 由评测模型 $\mathcal{J}$ 判定，是对人类判断的自动化近似。

由此得关键 / 补充事实命中率：

$$r_i^{K} = \frac{1}{\kappa_i}\sum_{f \in K_i} \delta_i(f), \qquad r_i^{S} = \frac{1}{\sigma_i}\sum_{f \in S_i} \delta_i(f)$$

**空集约定**：$\kappa_i = 0 \Rightarrow r_i^{K} := 1$；$\sigma_i = 0 \Rightarrow r_i^{S} := 1$（无该类事实不罚分）。

## 4. 硬门槛（Hard Gate）

$$\gamma_i \in \{0,1\},\qquad
\gamma_i = \begin{cases}
1 & \hat{A}_i \text{ 为「实质作答且切题」} \\
0 & \text{否则（空/拒答/反问澄清/答非所问）}
\end{cases}$$

由评测模型判定：$\gamma_i = \mathcal{J}_{\mathrm{gate}}(Q_i, G_i, \hat{A}_i)$。

## 5. 单题答案正确性（Per-Item Correctness）

$$\boxed{\,c_i = \gamma_i \cdot \big(0.8\, r_i^{K} + 0.2\, r_i^{S}\big) \in [0,1]\,}$$

- 权重 $0.8 / 0.2$：关键事实决定答案是否成立，占主导；
- $\gamma_i = 0 \Rightarrow c_i = 0$（整题归零，不因事实命中而豁免）；
- 因「实质作答」必过门槛（$\gamma_i = 1$），故对实质作答题恒有 $c_i = 0.8\, r_i^{K} + 0.2\, r_i^{S}$。

## 6. 题目分类（Labeling）

$$\ell_i = \mathcal{J}_{\mathrm{classify}}\big(\hat{A}_i,\ \gamma_i,\ e_i\big) \in \{\text{answered},\ \text{refusal\_gap},\ \text{clarify\_missing},\ \text{e2e\_error}\}$$

| 分类 $\ell_i$ | 含义 | 是否计分 |
|---|---|---|
| `answered` | 实质作答（⟹ $\gamma_i = 1$） | ✅ 计入均值 |
| `refusal_gap` | 缺口拒答（$\gamma_i = 0$，归因：知识库无题目所需内容） | ⛔ 仅记录 |
| `clarify_missing` | 需澄清（$\gamma_i = 0$，归因：反问 / 需用户补信息） | ⛔ 仅记录 |
| `e2e_error` | 端到端链路异常（未产生可评分回答） | ⛔ 仅记录 |

## 7. 聚合口径（Aggregation）

**主指标（实质作答口径）**——只统计已实质作答的题目：

$$I_{A} = \{\, i : \ell_i = \text{answered}\,\}, \qquad
\bar{C} = \frac{1}{\lvert I_A \rvert} \sum_{i \in I_A} c_i$$

**全量口径（对照）**——拒答 / 澄清 / 异常按 0 分计入：

$$\bar{C}_{\mathrm{full}} = \frac{1}{N} \sum_{i=1}^{N} c_i$$

**分域聚合**——对域（sheet）$d$：

$$\bar{C}_{d} = \frac{1}{\lvert I_A \cap d \rvert} \sum_{i \in I_A \cap d} c_i$$

**跨批次合并**等价于对全部实质作答题取均值（逐题同权，非按域加权）：

$$\bar{C}_{\mathrm{merge}} = \frac{1}{\lvert I_A \rvert} \sum_{i \in I_A} c_i = \bar{C}$$

## 8. 当前结果（合并 372 题）

$$N = 372,\qquad \lvert I_A \rvert = 292,\qquad \bar{C} = 66.0\%, \qquad \bar{C}_{\mathrm{full}} = 51.8\%$$

| 域 $d$ | 题数 | $\bar{C}_d$ |
|---|---|---|
| 运营平台 | 112 | 79.2% |
| MCX | 30 | 76.9% |
| 调度台 | 83 | 69.0% |
| 定位产品 | 73 | 57.4% |
| miniserver | 29 | 50.1% |
| MDM | 45 | 42.6% |

---

## 9. 边界与诚实声明

1. $\delta_i(\cdot)$ 与 $D(G_i)$ 由评测模型 $\mathcal{J}$ 自动化近似，非甲方人工标注；个别题的拆解可能偏离人工判断。
2. 命中判定为语义等价判定，依赖 judge 一致性（同题重测存在随机性）。
3. 摸底口径：系统未导入新表，直接以现有知识库作答；低分主要由「未导入新表」主导，属知识库覆盖率缺口，非纯回答质量。
4. 该数字为内部质量摸底，与合同验收指标（引用正确率 ≥95%、答案可接受准确率等）口径不同，不可混同。
