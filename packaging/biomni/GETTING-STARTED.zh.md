# RBPfinder（Biomni / Linux 版）傻瓜式上手

> 要给人看的排版版本（内容是这份文档的精简版：安装 / 使用 / 输出）：
>
> - PDF：`python3 packaging/biomni/make_guide_pdf.py`
> - Word（可编辑，带导航窗格大纲）：`node packaging/biomni/make_guide_docx.js 指南.docx`

对应版本 **v1.2.8+linux**。权威内容在仓库里，这份文档随代码一起更新 —— 如果哪天它和
`packaging/biomni/README.md` 冲突，以 README 和代码为准。

---

## 0. 三十秒：它是什么，不是什么

**是什么**：给一个已注释的噬菌体基因组，输出一份**按证据分层的受体结合蛋白候选清单** ——
先测哪几个、哪几个是为了保召回留着的、以及这份清单有多完整。

**不是什么**：

- **不做注释。** 注释是 Biomni 的活（pharokka / phold）。这是刻意的分工 —— 如果
  RBPfinder 自己注释，pharokka 或 PHROGs 升级就能悄悄移动一个分层结果，而没人会在
  diff 里看见。
- **不联网。** 所有远程提交路径都被拒绝（退出码 2，在读取任何序列之前）。序列不离开这台
  机器，没有例外，没有开关。
- **不保证找全。** 证据不足时它输出 `completeness: limited` 并说明原因，而不是凑一份好看
  的清单。**这个状态是结果的一部分，要读。**

**适用范围**：有尾双链 DNA 噬菌体（Caudoviricetes）。RNA 噬菌体、单链 DNA、丝状、无尾、
古菌病毒都不在验证范围内 —— 拿这些来跑，要在跑之前说明，不是跑完再说。

---

## 1. 安装

先判断你是哪种情况，三种的指令完全不同。

### 情况 A：全新沙箱

把这段发给 Biomni：

```
https://github.com/Yarkev/RBPfinder 这是一个 skill，装到这台沙箱上。
克隆后读 packaging/biomni/README.md 按步骤做，特别注意这三节：
 - "Copying the files is NOT registration"：复制文件和 check_skill 都不是注册
 - "When the Foldseek CDN refuses you"：结构库可能被限流，里面有四条替代路径
 - "2a. Verify the deployment"：装完必须验证，不能假设
```

会花掉的时间主要是下载：结构库 pdb100 约 6.5 GB、ProstT5 权重约 2.3 GB。

### 情况 B：沙箱已经配好，只是更新版本

```
把 RBPfinder 更新到最新版并重新部署 skill。

cd /workspace/rbpfinder_src && git fetch origin && git checkout main
source /workspace/rbpfinder.env
uv pip install --force-reinstall --no-deps -e /workspace/rbpfinder_src

然后重新部署 skill 包（README 第 2 节的 cp），过 check_skill，
并走平台注册流程生成更新卡片 —— 卡片我来确认。

最后跑一次任意基因组，报告 run_result.json 的 versions 字段，consistency 必须是 "ok"。
```

**`--force-reinstall` 不能省。** 可编辑安装下 `git checkout` 只换文件、不换安装元数据，
所以运行会报出一个过期的版本号。工具现在会自己发现并在 `versions.consistency` 里说出来，
但重装一次就不用看它报警。

### 情况 C：机器被重建了（OOM / delete）

```
这台机器被重建了，恢复 RBPfinder 环境。
/shared 上有结构库和 pharokka 库的 tarball 备份，用恢复脚本从那里恢复 ——
不要去跑 foldseek databases，那个 CDN 对本机 IP 限流。
然后 checkout main、重装（editable + force-reinstall）、重新部署并注册 skill，
最后 rbpfinder doctor 期望四项全 AVAILABLE。
```

**第一句就把 CDN 排除掉**，否则 agent 会老老实实去重试半天。

---

## 2. 验证装对了（别跳过）

```bash
rbpfinder doctor
```

四项核心该是 `AVAILABLE`：rules、schema、Local Stage 2、Local Stage 3。

然后跑一次任意基因组，看这个字段：

```bash
python -c "import json; print(json.load(open('<输出目录>/run_result.json'))['versions'])"
```

| 看到 | 意思 | 做什么 |
|---|---|---|
| `consistency: ok` | 装对了 | 继续 |
| `consistency: package_metadata_stale` | 代码是新的，安装元数据是旧的 | 按 `note` 里的话重装 |
| `consistency: not_installed` | `0+unknown`，根本没装上 | 重装 |

**为什么这步重要**：有一次整轮测试跑在了旧代码上并被报告为通过 —— 一个非可编辑副本遮蔽了
可编辑路径，`git checkout` 对实际运行的东西毫无影响，而输出里没有任何迹象。这个检查就是
为那件事存在的。

---

## 3. 用

### 最短的指令

```
找出 /data/01F2.fasta 里的受体结合蛋白候选。宿主是 Salmonella。
```

不用提工具名，skill 的描述里有"受体结合蛋白 / tailspike / tail fibre / depolymerase"
这些词，平台会自己匹配。

### 但这三件事值得每次都说

```
用 RBPfinder 分析 /data/01F2.fasta。
宿主 Salmonella。
走 accurate 路线（跑 phold）。
基因组是环状。
```

| 说什么 | 不说会怎样 |
|---|---|
| **fast 还是 accurate** | skill 会主动问你，说了省一轮。**这个差别比听起来大** —— T7 上它就是文献确立的 RBP 在不在清单里的区别 |
| **宿主属名** | 不会出错，但少一层证据。只在配了对应宿主库且覆盖该菌属时才用得上，否则记录并跳过 |
| **是否环状** | ⚠️ **最容易悄悄吃亏的一条**。GenBank 没声明 topology 时必须显式说。尾模块跨过原点时 C1/C3 提名会丢，**而池大小不变**（别的通道补上了同一个 CDS），所以没有任何打印出来的数字会暴露这个退化 |

### fast 和 accurate 怎么选

| | fast（默认） | accurate |
|---|---|---|
| 注释 | pharokka | pharokka + phold（全基因组） |
| 额外耗时 | — | 8 核上 1–2 小时 |
| 买到什么 | — | **更好的注释**：解决 pharokka 留作 "hypothetical" 的 CDS |

实测在 T7 上：

```
fast      Primary: CDS_0039, CDS_0040              gp17 不在清单里
accurate  Primary: CDS_0039, CDS_0040, CDS_0052    gp17 在清单里（它就是文献 RBP）
```

**注释差的基因组选 accurate。** 一小时的 CPU 对一份错的清单来说很便宜。

phold **不会**增加第二个独立的结构证据家族 —— 去相关会把 phold 命中和 Foldseek 命中
折叠成同一个家族，这是设计。

---

## 4. 读结果

按这个顺序读，用这些名字：

1. **Primary** —— 优先去测的候选
2. **Rescue** —— 为保召回留着的，不是因为证据强
3. **Completeness** —— `complete` / `partial` / `limited`，连同它给的原因

**内部候选池不是实验清单。** 它是为算法设计的召回保护装置（可能有十几二十个 CDS），
把它当成要去测的清单是最有后果的误读。

### 分层怎么读

```
tier_r   R1  至少 2 个受体结合家族，且含一个直接家族（T/D/S）
         R2  可能是 RBP / 召回保护
         R3  属于受体结合装置，但没有直接的识别证据
         N   有正面证据表明它不是 RBP

tier_e   E3  至少 3 个独立证据家族     E2  2 个     E1  1 个或未解决
```

`tier_r` 是"它是什么"，`tier_e` 是"我们看得多仔细"。**两者分开，所以一次证据不足的运行
不会被误读成一个有信心的否定结论。**

### 拿到结果后该追问的两句

```
versions 和 vocabulary 字段是什么？
```

决定这批数字以后还能不能和别的批次比。

```
presentation_signals 里有没有触发？term_trigger_counts 是多少？
```

前半是候选校正提示（部件名假阳性、伴侣邻接、方向冲突），后半是词表校准数据。

### Primary 是空的，怎么办

**空不等于"这里没有 RBP"。** 先看哪些证据家族根本没跑起来：

- `stage2_local` —— 配了序列数据库吗？
- `stage3_local` —— 本地结构搜索跑了吗，搜了多大范围？
- `limitations` —— 哪些审计报告自己无法执行？

然后判断是哪一种：**证据饥饿**（S 和/或 D 从来没跑，所以没东西能到达进 Primary 的层级）
还是**基因组本身确实没解决**（全都跑了还是没有合格的）。

**把没跑起来的家族和结果一起报告。** 不要把一次饥饿的运行当成生物学发现。

---

## 5. 出问题了：按症状查

| 症状 | 原因 | 怎么办 |
|---|---|---|
| `Local Stage 3 UNAVAILABLE` | foldseek / 结构库 / ProstT5 缺一个 | 看 README 第 1 节 |
| `probability_pct` 全是 null | **预期行为**，ProstT5 路线上序列没有骨架坐标，算不出概率 | 什么都不用做，别重试 |
| `A2 could not run: ...missing_genomewide_T_or_M` | 结构搜索是池范围的 | **这是默认路线的设计行为，不是故障**。说明完整性上限即可 |
| exit 2 提到 schema validation | 内部契约违规 | 报告它，**用户的输入没问题** |
| `stage3_local.cds_searched: 0`（Mode B） | phold TSV 没有蛋白序列列 | 用 `--genbank phold.gbk`，或加 `--phold-genbank phold.gbk` |
| `Cannot open lookup file ..._ca.lookup` | 下载结构库时前缀含了目录成分，foldseek 自己的安装脚本 `mv` 失败 | 进到目标目录里用扁平前缀重下 |
| foldseek 下载 429（`error code: 1027`） | CDN 对数据中心 IP 限流 | README "When the Foldseek CDN refuses you" 一节有四条路 |
| `pharokka install` 报 MD5 不符 | Zenodo 对数据中心 IP 返回 403，下到的是几百字节的拦截页 | **先看文件大小**，再走手动安装 |
| phold 报 `np.long` AttributeError | numpy/scipy 版本混装 | `pip install phold==1.2.5` 并固定 `numpy<2`、`transformers==4.46.3` |
| 昨天还在的工具不见了 | `/opt/conda` 和 apt 装的包不过休眠 | 所有前缀环境放 `/workspace` |
| 结果像是旧代码跑的 | 非可编辑副本遮蔽了可编辑路径 | 见上面第 2 节 |

---

## 6. 不该做的事

**不要改 `rules/decision_rules.yaml` 的阈值来让运行成功或结果好看。** 一个为了迁就答案而
改动的阈值，不再是任何东西的证据。

**不要让 agent 自己读证据重新排名。** 如果它说"虽然工具给了 R3，但我认为这个更可能是
RBP"，它在用工具的权威包装一个猜测。

它**应该**做的是：如果文献确立的 RBP 没进 Primary，**明确指出这个分歧并解释机制**
（通常是部件名保守性加证据饥饿），然后说清它没有改变工具的结论。**指出分歧不是重新排名。**

**不要手工从 PDB 文件建结构库。** `foldseek createdb` 会成功，但结果没有 taxonomy 文件，
搜索会在运行时死掉。`doctor` 把这种库报成 `CONFIGURED, INVALID` 是刻意的。

**不要把 `--phold` 的 TSV 当输入然后期待结构证据。** 那个表格没有蛋白序列列。

---

## 7. 批量跑的时候

```
对 /data/genomes/ 下所有基因组跑 RBPfinder，accurate 路线，
每株记录形态分类（sipho / myo / podo）和 run_result.json 的
versions / vocabulary / presentation_signals。
跑完汇总：
  python3 packaging/comparison/term_noise_floor.py --aggregate <runs 目录>
```

**记录形态分类那一句零成本，但值得加。** 现在最大的未知是"长尾噬菌体的召回有没有系统性
偏低" —— `A_sipho_gpJ` 这个架构类在规则里有自己的参数，而没有任何记录显示它被端到端跑过。
一株的结果看不出它所属的那一类被处理得比别的类差，所以这个问题只有批量跑 + 记录形态才能回答。

最后那条 `--aggregate` 会把六个"装配上下文"词的触发率汇总出来 —— 跨多株沉默的词可以剪掉，
在几乎所有株上都触发的词是噪音。这是零额外成本的校准。

---

## 8. 还有哪些已知的弱点

写在这里是因为知道它们比不知道好：

- **证据饥饿看起来像生物学发现。** 输出里确实说了（`stage2_local`、`limitations`、
  `capability_matrix`），但只有人去读才算。
- **部件名保守性。** "tail fiber protein" 是现实中最常见的 RBP 描述，而它在默认词表里的
  读法在不同家族之间不一致。这个分歧有时恰好给出正确答案，所以"统一词表"不是显然的清理。
- **所有阈值都是 `calibration: pending`** —— 专家推导的起点，不是拟合值。
- **展示信号的词表来自一株肌尾噬菌体。** 在短尾或长尾噬菌体上，信号一个都不触发意味着
  "不适用"，**不是"干净"**。
- **C6 / M 家族（外部机器学习）没有实现**，规则里有、通道里没有。

完整清单在仓库的 `WORKFLOW.md` §5，那一节专门列"错答案可能来自哪里"。
