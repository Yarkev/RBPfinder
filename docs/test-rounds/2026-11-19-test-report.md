# RBPfinder skill 测试与优化报告（草稿）

测试日期：2026-11-19。执行环境：Biomni 沙盒（16 CPU / 16 GB / Linux x86_64）。

## 1. 测试对象与环境

- Skill 包：`rbpfinder-biomni-skill-1.2.1linux.tar.gz`（SKILL.md + references/KNOW_HOW.md）
- 工具源码：https://github.com/Yarkev/RBPfinder @ `2cb9ed1`（branch `claude/loving-gauss-cl7oca`，v1.2.1+linux）
- WORKFLOW.md 对应 commit `5913622`（该分支上 `2cb9ed1` 的下一个提交，仅新增 WORKFLOW.md）

### 1.1 一致性预检（通过）

- 上传的 skill 包与仓库 `packaging/biomni/skill/rbpfinder` 在 `2cb9ed1` 上**逐字节一致**——未复现 WORKFLOW.md §5.9/§5.10 担心的文档-代码漂移。
- 仓库自带测试套件 196 项全部通过。
- `rbpfinder doctor`、`validate-input`、`--help` 均按文档工作。

## 2. 环境搭建

| 组件 | 版本 | 状态 | 备注 |
|---|---|---|---|
| rbpfinder | 1.2.1+linux @ 2cb9ed1 | OK | `pip install -e .`，依赖 PyYAML/jsonschema/biopython |
| blastp/makeblastdb | n/a | OK | 沙盒预装 |
| DB_PHAGE | RefSeq viral.1 蛋白（722,823 序列） | OK | `rbpfinder database configure phage` 正常 |
| foldseek | 4f5c00cc（静态 avx2） | OK | mmseqs.com 下载 |
| PDB100 | foldseek databases 官方下载 | OK（有坑，见 §4.3） | ~6.5 GB |
| ProstT5 权重 | f16 gguf | OK | ~2.3 GB |
| pharokka | 1.10.1（mamba） | OK | PHROGs 数据库下载受阻（Zenodo 403），见 §4.4 |
| phold | 1.2.6（mamba） | OK | `phold install` 下载数据库正常 |

注意：沙盒 home 不持久，`RBPFINDER_CONFIG` 必须显式指向持久路径（SKILL.md 已有此提示，实测属实）。

## 3. T7（NC_001604）基准回归

### 3.1 契约测试（全部通过）

| 契约 | 预期（SKILL.md/WORKFLOW.md） | 实测 |
|---|---|---|
| `--allow-remote` | exit 2，ingest 前，明确拒绝信息 | 一致 |
| `--data-policy remote_allowed` | exit 2 | 一致 |
| `--stage2-backend ebi` | exit 2（`--help` 仍列出该选项） | 一致 |
| 裸 FASTA | 拒绝并打印注释指引 | 一致 |
| 非 `CDS_<n>` locus tag | 按坐标重编号 + 保留 raw_locus_tag + 警告 | 一致（T7p01→CDS_0001） |
| `run_result.json` | 31 顶层键、versions/vocabulary | 实测 32 键（见 §5 待核对） |
| versions.package | 非 `0+unknown` | `1.2.1+linux` |
| exit code 语义 | 0/1/2/3 | 观察到 0 与 2，符合 |

### 3.2 证据阶梯（核心回归）

| 配置 | Primary | gp17 (T7_CDS_0052) | completeness |
|---|---|---|---|
| 无任何数据库 | 0 | R3-E1，G，unresolved | limited |
| + S（RefSeq viral BLAST） | 2：CDS_0039/0040（host range and adsorption protein，R2-E2） | R3-E2，G+S，unresolved | limited |
| + S + T（Foldseek PDB100/ProstT5） | 同上 | R3-E3，G+S+T，unresolved | partial |
| + `--foldseek-all-cds` | 同上（池 21→49，A2 resolved，terminated_cleanly=True） | R3-E3 | partial |

与文档一致的行为：

- 无库时 Primary 0 + `completeness: limited` + limitations 明示 —— 证据饥饿被如实报告，未被包装成生物学结论。
- T family 单独不抬 tier：gp17 的 PDB 命中标题（"N-terminal of mature bacteriophage T7 tail fiber protein gp17"）读作 apparatus，与"T alone rarely lifts a true RBP"一致。
- `--foldseek-all-cds`：A2 从 unexecutable 变 resolved，池重开 28 个 CDS，Primary 不变 —— 与"重开多、晋升少"的试点结论一致。
- `terminated_cleanly=False` 在默认路径出现，来源是 A2 自报 unexecutable —— 设计行为，非缺陷。
- C5 比较通道：3 个 Teseptimavirus 近缘基因组入组（K1E 因过远排除），无可变位点 —— 正常执行。

### 3.3 未复现的文档声明（重要）

WORKFLOW.md §2 Stage 2 称："the measured T7 gp17 case moved R3 → R2 and into Primary on nothing but a configured phage database"。实测未复现：配置 RefSeq viral.1 全库后 gp17 仅 R3-E1→R3-E3，始终未进 Primary。

机制（已从 run_result.json 逐条核实）：

1. gp17 的 S family top-10 命中全部是 "tail fiber protein" 注释（含自身 NP_042005.1，100% identity），共识 `apparatus=10` → S 记录 direction = `supports_apparatus_membership`（moderate）。
2. C2 关键词（"fib, fiber"）给出 `supports_receptor_binding` 但 strength=weak，且在 family G 内不独立计数。
3. R1 要求 ≥2 RB family 且含 direct 证据；gp17 三个 family 全部读 apparatus，故封顶 R3。

推论：该文档声明的复现依赖于**含功能注释的 curated 库**（如同源蛋白被注释为 "receptor-binding/adsorption"），而非任意 phage 蛋白库。实测中进入 Primary 的 CDS_0039/0040 正是靠 "adsorption" 这一功能词获得 strong S 记录。**建议**：WORKFLOW.md/SKILL.md 应写明 DB_PHAGE 的注释质量决定该增益是否出现，并给出 curated 库的构建建议。

### 3.4 accurate 路线（phold）——文档承诺的增益在此实现

phold 1.2.6（CPU，8 线程）对 T7 GenBank 运行 136 秒；`phold.gbk` 通过 validate-input，RBPfinder 正常摄入。

| 配置 | Primary | gp17 (T7_CDS_0052) |
|---|---|---|
| fast 路线（RefSeq 注释 + S + T） | CDS_0039/0040（R2-E2） | R3-E3，unresolved |
| **accurate 路线（phold.gbk + S + T）** | **CDS_0039/0040（R1-E3）+ CDS_0052（R2-E3）** | **R2-E3，进 Primary** |

机制（run_result.json 逐条核实）：phold 的 PHROG 注释（phrog:4242）产生 **D family** 记录，D 术语表把 "tail fiber protein" 读作 `supports_receptor_binding`（moderate，direct family）。gp17 由此获得 1 个 direct RB family（D）+ 3 个 apparatus family（G/S/T）→ R2（不足 2 个 RB family，故非 R1）。

**两个文档声明的实测裁决**：

1. WORKFLOW.md 把 "gp17 R3→R2 进 Primary" 归功于 DB_PHAGE——**用 RefSeq viral 全库未复现**（§3.3）；改用 phold 注释后**实现了**。文档应把该增益归因于"带 PHROG/功能注释的注释源"，而非任意 phage 库。
2. 已知问题 #2 的术语表分歧被直接实测：同一个词 "tail fiber protein"，D（PHROG 关键词）读 receptor_binding，S（同源描述）读 apparatus——两张术语表确实不一致，且这次分歧恰好把 gp17 抬进 Primary。

### 3.5 `--vocabulary` 对比

| 模式 | fast 路线（S+T）Primary | accurate 路线 Primary | gp17 |
|---|---|---|---|
| current（默认） | CDS_0039/0040 | 3 个（含 gp17） | R2-E3 |
| unified | CDS_0039/0040 | 2 个（gp17 跌出） | R3-E3 |
| explicit_guard | CDS_0039/0040 | 3 个（含 gp17） | R2-E3 |

**文档预测的风险被精确复现**：SKILL.md 称 "unified may undo the DB_PHAGE gain"——实测 unified 确实使 gp17 跌出 Primary。在 T7 这个文献金标准上，unified 给出生物学上错误的答案，current/explicit_guard 给出正确答案。这是术语表默认值决策（已知问题 #2 的 "default has not moved"）的第一份单菌株实测数据；48 菌株回归仍是缺口。

## 4. 发现的问题

### 4.1 工具缺陷

（待补：pharokka 全栈路线测试后汇总）

### 4.2 文档问题

1. **DB_PHAGE 增益声明过强**（§3.3）——WORKFLOW.md §2 与 SKILL.md "Bootstrap" 节应限定"取决于库的注释质量"。
2. `run_result.json` 顶层键数：WORKFLOW.md 说 31，实测 32（待核对是否文档笔误或版本差异）。

### 4.3 环境坑（建议写入 SKILL.md Troubleshooting）

1. **foldseek `databases PDB <dir>/<prefix>` 的安装脚本 bug**：前缀含目录成分时，下载完成后 `cd`/`mv` 失败，文件散落在 tmp 目录与工作目录，数据库不可用且报错晦涩（`Cannot open lookup file ..._ca.lookup`）。**规避**：用扁平前缀（`foldseek databases PDB pdb100 tmp`，在目标目录内执行）。
2. **Zenodo 对数据中心 IP 403 限流**：pharokka PHROGs 数据库下载受影响；`pharokka install` 的报错是误导性的 MD5 mismatch（实际下载到的是 763 字节的 403 页面）。建议 pharokka 文档/错误信息区分"下载失败"与"MD5 不符"。
3. NCBI RefSeq viral 蛋白文件名是 `viral.1.protein.faa.gz`（分卷），不是 `viral.protein.faa.gz`。

### 4.4 未完成项（经用户确认跳过）

- **pharokka 全栈注释路线**：PHROGs 数据库无法从 Zenodo 下载（本网络 403 限流，12 次重试 × 2 个 URL 均失败）。经用户确认跳过。RBPfinder 的输入接口已由 RefSeq GenBank 与 phold GenBank 两条路验证；唯一未测的是 pharokka 自身输出的 GenBank 摄入（风险低——pharokka 输出是标准 GenBank，且 validate-input 用同一读取器）。
- **用户真实基因组测试**：等用户提供文件后可随时运行（环境已就绪）。

## 5. 优化建议清单

### 文档修订（SKILL.md / KNOW_HOW.md / WORKFLOW.md）

1. **DB_PHAGE 增益的归因**：WORKFLOW.md §2 与 SKILL.md Bootstrap 节把 "gp17 R3→R2 进 Primary" 归于"配置 phage 数据库"；实测表明增益取决于**注释质量**——RefSeq viral 全库（同源注释全为部件名）不产生该增益，phold/PHROG 注释才产生。建议改写为"配置含功能注释的库（PHROG/phold 注释或 curated 功能注释）"。
2. **`run_result.json` 顶层键数可变**：`top_unresolved_candidates` 仅在 shortlist 为空或 completeness=limited 时输出（presentation-only，cli.py:774 的 trigger 逻辑），故键数为 31 或 32。文档"31 top-level keys"建议加"（随 completeness 状态 ±1）"。
3. **Troubleshooting 新增条目**：
   - foldseek `databases PDB <dir>/<prefix>` 前缀含目录成分时安装脚本 mv 失败，数据库损坏且报错晦涩（`Cannot open lookup file ..._ca.lookup`）；规避：在目标目录内用扁平前缀。
   - Zenodo 对数据中心 IP 403 时，pharokka `install` 报误导性 MD5 mismatch（实际是 763 字节 403 页面）；先检查文件大小。
   - NCBI RefSeq viral 蛋白为分卷 `viral.N.protein.faa.gz`。

### 工具层面（建议，不擅改）

1. **术语表分歧（#2）的默认值决策**：现有第一份单菌株数据（T7：current/explicit_guard 正确，unified 错误）。建议运行 48 菌株回归后再定；若回归结果一致，`explicit_guard` 可能是比 `current` 更可辩护的默认（行为相同但语义更明确）。
2. **decorrelate 强度优先（#3）**：T7 上 gp17 的 weak receptor-binding 关键词记录被 moderate apparatus 记录压制——但 phold 路线引入 D family 后此问题不再影响结果。维持"改排序键需基准"的原判。
3. pharokka `install` 的 MD5 错误信息应区分"下载失败（非 tar.gz）"与"MD5 不符"——这是上游 pharokka 的问题，可在 SKILL.md Troubleshooting 提示。

### 已核实无需修改

- 全部退出码语义、离线拒绝、FASTA 拒绝、locus tag 重编号、`--host` 降级、`--suppress-nomination`、`--foldseek-all-cds` 的 A2 行为、`terminated_cleanly` 语义、C5 入组逻辑——均与文档一致。

## 5. 优化建议清单

（汇总后填写）
