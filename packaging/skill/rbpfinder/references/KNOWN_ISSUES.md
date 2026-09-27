# RBPfinder v1.2.0 — 已知问题

写在这里的都是**已经测到、尚未解决**的事。发布不隐藏它们。

---

## 1. Windows 长路径 / MAX_PATH

在很深的目录里 `pip install .` 可能失败：

```
OSError: [WinError 206] 文件名或扩展名太长
  ...\site-packages\numpy-2.5.2.dist-info\licenses\numpy\_core\src\common\pythoncapi-compat
```

来自 numpy 的深层 license 文件，不是 RBPfinder 本身。

**规避**：装到短路径（如 `C:\rbpfinder\venv`），或在 Windows 中启用长路径支持。

## 2. 仅 Windows 已验证

Linux / macOS **not yet validated**。代码没有已知的平台相关逻辑，但**没有测过就不承诺**。
Python 看起来跨平台不等于可以顺手宣称支持。

## 3. 自动网络获取：实证已补齐，起因是传输层缺重试

```
M15C implementation        FROZEN + 一次窄修（transport retry）
M15C offline correctness   PASS   13/13 + 15/15 + 9/9 + data-preservation PASS
M15C network validation    PASS   2026-08-25  M15C_NETWORK_SMOKE=PASS
    rest_stream tiny real query        109 条真实记录，条数与盘上字节核对一致
    cursor_pagination tiny real query  109 条，与 rest_stream 独立一致
```

**这一项之前被记成 `PENDING_EXTERNAL`，那个定性是错的，现已撤销。**

M16 远程 backend 也已于同日完成首次真实提交（`M16D_FIRST_RECORDING=PASS`，
一次真实提交，72 命中，坐标硬 gate 在真实
响应上成立）。**范围仅限 transport/provider 契约层**：没有跑任何远程 benchmark，
`benchmark/fixtures/ebi/` 仍是合成的，只服务单元测试。

8-24 的判断是"`rest.uniprot.org` 在 TLS 握手阶段被关闭"，依据是单次探测。
8-25 每个 host 抽 10 次，结论完全不同：

```
host                      裸 curl   加有界重试
rest.uniprot.org            8/10       10/10
www.ebi.ac.uk               7/10       10/10
pypi.org                    5/10       10/10
```

本机出站 TLS 均匀丢 20–50% 的连接，与目标无关。失败长相一律是
`SSL: UNEXPECTED_EOF_WHILE_READING` / curl exit 35。同一个 URL 连抽三次可以
分别得到 TLS EOF、HTTP 406、HTTP 200。**瞬时传输失败被当成了 endpoint unavailable**，
并且被当作 release evidence 的免责理由用了一天。

真正的产品侧原因：`RestStreamTransport` 与 `DirectCompressedTransport` 没有传输级重试。
已按 YAML `database_acquisition.transport_retry` 窄修：

```
connection reset / TLS EOF / connect failed / timeout / empty reply / 429 / 5xx
                          → 有界退避重试（5 次，1/2/4/8 秒）
其他 4xx                   → 立即失败（那是关于请求本身的回答，不是坏连接）
完整性失败                 → 立即失败，且绝不因此更换 transport
已完成/已发布的库          → 数据保全不变式一字未动
未分类的 curl 退出码       → 不重试（白名单，不是黑名单）
```

同一次实证还暴露了第二个只有真实查询才会现形的缺陷：`rest_stream` 把查询串直接拼进
URL，没有百分号编码。真实 UniProt 查询里的空格、括号和 `[150 TO 170]` 区间会被 curl
读成 glob range，URL 在**一个字节都还没发出去**之前就被拒（exit 3）。
所有 fixture 查询都太简单，藏住了这个问题。已修。

## 4. direct-compressed 对当前 UniProt taxonomy spec 会正常跳过

UniProt 的 taxonomy 查询目前没有可信的 bulk artefact（稳定 URL + checksum），
因此 `direct_compressed` 报 `unavailable`，`auto` 正常进入 `rest_stream`。

**这不是失败**，是 spec 里没有可信来源时的正确行为。plan/provenance 会写明：

```
method: direct_compressed
availability: unavailable
reason: no trusted bulk artifact defined for this DatabaseSpec
```

只有用户显式 `--method direct` 时，它才成为命令失败。

## 5. `direction_map` 本体仍有已知保守偏差（不阻塞 v1）

同一张词表造成两个方向相反的后果：

```
缺 integrase / anti-repressor / partition ATPase / head scaffolding 等非 RBP 词汇
  → unresolved-RBP-risk 区段被高估 → completeness 偏保守

"tail protein" / "head-tail adaptor" 被映射为 structural_only
  → 少数株即使 rank-1 候选就是真 RBP，也拿不到 receptor-binding 方向 → 空清单
```

**失效方向是偏保守，不是危险的假 complete**——`false-complete = 0` 在 48 株上成立。
产品风险上这两者不对称：v1 宁可多报 `limited`。

空清单株已有展示层补丁（`top_unresolved_candidates`），不改任何科学判断。
本体扩充影响 evidence → tier → Primary/Rescue，**必须单独立项并跑完整闸门链**，
列在 v1 之后的 backlog。

## 6. A2 / T / M 能力不完整时 `assessment_coverage = reduced`

本机没有全基因组 T/M 来源，审计 A2 无法运行，因此：

```
assessment_coverage: reduced
```

它**封顶** completeness（不允许裸 `complete`），但**绝不单独制造 `limited`**。
这是安装的属性，不是噬菌体的属性——装备不足的机器上一株解析良好的噬菌体，
与一株真正难解的噬菌体不会显示成同一个东西。

---

## 7. `raw.evalue = null` — 已修复的 schema 违规（2026-09-03，M20-b2E4）

**这是一个真实的产品缺陷，不是 E4 的副作用。** `domain_profile.from_interproscan` 在
命中没有 e-value 时把 `raw.evalue` 写成 `null`，而 `evidence_schema.json` 把该字段定义为
`number`。这条记录**一直是非法的**。

它之所以从未暴露，是因为旧实现每份 artifact 只吐**一条被选中的代表记录**，而被选中的那条
（最具 RBP 信息量的命中）几乎总是带 e-value。E4 让每个命中各成一条记录之后，
PANTHER / Gene3D 这类没有 e-value 的条目才变得可达，schema 闸门在第一次端到端运行就失败。

```
旧实现已存在 schema 违规
  ↓
representative-hit selection 把它遮住了（非法状态不可达）
  ↓
E4 扩大可达状态集合
  ↓
既有非法状态变得可见 → schema gate 才真正触发
```

修法：没有 e-value 就**不写这个键**（absent ≠ null，只有 absent 合法）。
冻结的 `scripts/pre_e4_localisation.py` **保留**这个缺陷，因为它是历史记录。

**这个模式还会再出现**，HHpred 与 Foldseek 多记录化时尤其如此。所以留了一条常设闸门：
`scripts/test_evidence_record_schema.py` —— 对**每条 reader 产出的 EvidenceRecord**
做 schema 校验，而不是只校验最终装配好的 run_result。一条记录可能在进入输出之前就被丢弃、
被 supersede 或被竞争掉，只验最终产物抓不到它。该闸门自带反例（null evalue、未知 family
必须被拒），保证它不是恒真。

## 8. `rbp_evidence_localisation` 的内容含义已改变（2026-09-03，M20-b2E4）

**不是 schema 迁移** —— 字段类型仍是 `string`，旧的消费方不会解析失败。但**内容的含义变了**，
升级时值得知道：

```
改之前   min(start)..max(end)，对单条被去相关标记的记录取外包络
         例：aa 111-667
改之后   携带受体结合证据的**离散 span 列表**，可能不止一个
         例：aa 111-667, 116-665
```

原来的写法会断言"最左命中到最右命中之间**全部**是证据"，而中间空隙没有任何东西支持。
对多结构域尾部蛋白来说，那些空隙恰恰是**别的**结构域所在。实测：在 P22 tailspike 上，
对保留下来的 span 取外包络会得到 `2-667`，即整条 667 aa 蛋白 —— 等于什么定位信息都没给。

**如果你的下游脚本按 `aa (\d+)-(\d+)` 单段解析这个字段**，请改成按逗号切分后再解析；
只取第一段会静默丢掉其余区域。

外包络本身没有被删掉，但降级为**仅供显示**的摘要：它不得作为 EvidenceRecord 的
canonical region，也不得进入 tier / family / role / 定位真值（见 `hull_principle`）。

## 不属于缺陷的行为

以下是设计选择，不要当成 bug 报：

```
未提供 --host          → DB_HOST 被跳过并记录原因；宁可少用，不用错宿主库
证据不足               → completeness: limited + 具体理由，而不是扩大 Rescue 让召回看起来完整
可选能力缺失            → doctor 退出码仍是 0；只跑 Mode A 不需要 BLAST 或数据库
网络不可用              → 明确报 TransportFailure；已有数据库与核心分析不受影响
```
