# RBPfinder — 用户指南

给定一个有尾 dsDNA 噬菌体基因组，生成**高召回、可解释的 RBP 实验候选清单**。

本文只写普通用户需要的那一条路径。开发脚本、manifest 生成器、benchmark 命令
不在这里——它们在 `DEVELOPER.md`。

---

## 1. 安装

```bash
pip install .
```

依赖（PyYAML / jsonschema / biopython）会自动装上，判据文件随包分发，
**不需要设置 `PYTHONPATH`，也不需要传 `--rules` / `--schema`**。

> Windows 已验证。Linux / macOS 尚未测试，标为 *not yet validated*。

## 2. 检查环境

```bash
rbpfinder doctor --require local_stage2
```

它只报告，不安装、不下载、不建库、不改环境。输出示例：

```
Local executables
  blastp                 AVAILABLE   path -- ...\blastp.EXE
  makeblastdb            AVAILABLE   path -- ...\makeblastdb.EXE

Databases
  DB_PHAGE               AVAILABLE   config -- metadata found; verified with blastdbcmd -info
  DB_HOST                AVAILABLE   config -- metadata found; verified with blastdbcmd -info
                                     host scope: Stenotrophomonas

Analysis capabilities
  Mode A GenBank         AVAILABLE
  Local Stage 2          AVAILABLE
  C5 comparative         AVAILABLE
```

`host scope` 告诉你这个宿主库是**为谁准备的**。doctor 不判断它是否适用于你手上的噬菌体——
那要看运行时你给的 `--host`（见第 4 节）。

缺少可选能力**不算安装失败**：只跑 Mode A 的用户不需要 BLAST，也不需要数据库，
此时 `rbpfinder doctor` 退出码仍是 0。

## 3. 注册数据库（只在首次安装做一次）

```bash
rbpfinder database configure phage <DB_PHAGE_prefix>
rbpfinder database configure host  <DB_HOST_prefix>
rbpfinder database show
```

`configure` 只记录路径，**不复制数据库、不联网**。它每次读取都会重新验证，
所以库被删除或移动后立刻显示 `CONFIGURED, INVALID`，而不是继续显示注册当时的状态。

## 4. 运行

```bash
rbpfinder --genbank phage.gbk \
          --host "Stenotrophomonas maltophilia" \
          --out results/
```

这就是完整命令。架构会自动判定，数据库会自动使用。

### `--host` 为什么重要

DB_PHAGE 对任何有尾噬菌体都适用，会自动使用。
**DB_HOST 是一个细菌属的库，只有在它覆盖你声明的宿主时才会被使用。**

| 你给的 `--host` | 实际搜索 | 结果 |
|---|---|---|
| 与宿主库匹配 | DB_PHAGE + DB_HOST | 完整证据 |
| 未提供 | 仅 DB_PHAGE | 记录 `skipped_due_to_host_context` |
| 与宿主库不匹配 | 仅 DB_PHAGE | 不会拿错误宿主的库去搜 |

差别是实打实的。同一条真实蛋白 `W073cp2a2_CDS_0005`：

```
给了正确的 host    R1-E3   local_blast_db   supports_receptor_binding   （sialidase 证据）
没给 host          R2-E3   local_blast_db   apparatus_membership
```

宿主库里的 exo-alpha-sialidase 同源是把它判定为受体结合蛋白的关键证据。
不声明宿主，这条证据就拿不到——所以 `--host` 不是可有可无的装饰参数。

## 4a. 本地结构证据（Local Stage 3，Linux）

**这是把"找不准"真正改好的一步。** R1（强 RBP）要求至少两个独立证据家族、且含一个
直接家族（T 结构 / D 结构域 / S 序列同源）。只有基因组上下文时，真正的 RBP 往往停在
R2/R3，上不了实验清单；而光靠上下文窗口又会把邻近的结构蛋白一起拉进来。**结构证据
（家族 T）正是把这两种错都掰正的那条线。**

过去结构证据走手动：导出 batch → 在网站上建模、检索 → 下载填回。在能装软件的 Linux
服务器上，可以让 Foldseek **在本机直接算完**，不碰任何网页、不花 token——它用 ProstT5
把氨基酸序列就地转成结构字母，再对本地结构库检索（和 phold 用的是同一条 ProstT5 路线）。
本机运行、**不传输任何序列**，所以 `private` 策略下就允许；`import_only` 下会被拒。

一次性准备（装软件、放库，几十 GB）：

```bash
# 1. 装 foldseek（Linux 原生）
conda install -c bioconda foldseek      # 或从 github releases 下二进制

# 2. 下结构库（功能库 pdb100 是判定 RBP 功能身份的关键；cath50 可选）
foldseek databases PDB100 /data/foldseek/pdb100 tmp

# 3. ProstT5 权重（让序列直接可搜，不需要先跑 AlphaFold）
foldseek databases ProstT5 /data/foldseek/prostt5 tmp
```

告诉 RBPfinder 它们在哪，用环境变量（一次设好）或命令行参数（每次给）：

```bash
export RBPFINDER_FOLDSEEK=foldseek
export RBPFINDER_FOLDSEEK_DB="pdb100=/data/foldseek/pdb100"
export RBPFINDER_PROSTT5_MODEL=/data/foldseek/prostt5
rbpfinder --genbank phage.gbk --host "..." --out results/
```

```bash
# 或逐次显式给（--foldseek-db 可重复）
rbpfinder --genbank phage.gbk --out results/ \
          --foldseek /opt/foldseek/bin/foldseek \
          --foldseek-db pdb100=/data/foldseek/pdb100 \
          --prostt5-model /data/foldseek/prostt5
```

先 `rbpfinder doctor` 确认三样都 AVAILABLE、`Local Stage 3` 显示 AVAILABLE 再跑。
数据库的 `label` 必须是 Stage 3 认识的名字（`pdb100`、`cath50` …）；用别的名字会被拒绝，
而不是搜完再悄悄丢弃。结构库检索结果写进 `results/stage3_local/`，每个 CDS 一个 JSON
（**没命中就是空 results 的 JSON，不是缺文件**——"查过没中"和"没查过"在盘上可区分），
并以 `foldseek_json` 的身份进入与手动导入完全相同的证据管线。关掉本步用 `--no-local-stage3`。

效果（合成算例，尾丝蛋白 CDS）：

```
无结构证据        R2-E2   families D,G
本地 Foldseek 后   R1-E3   families D,G,T   ← 结构命中补齐了直接家族 T，进入 Primary
```

> **跨平台说明**：Foldseek 没有原生 Windows 版，本步因此是 **Linux 专用**。Windows 上
> 结构证据仍走手动网页路线。这也是把 RBPfinder 搬到 Linux 云服务器的主要动机。

## 4b. 批量处理多株基因组

手上有几十株噬菌体时，不需要逐株手动敲命令：

```bash
rbpfinder bulk run --manifest genomes.tsv --out-root results/
```

`genomes.tsv` 是一张两列的清单（可以加第三列 `extra_args` 放额外参数，比如
`--host "Stenotrophomonas maltophilia"`）：

```
phage_id	genbank_path
W073cp2a2	genomes/W073cp2a2.gbk
N136cp1a1	genomes/N136cp1a1.gbk
```

每一株的输出仍然是独立的 `results/<phage_id>/`，和单株运行完全一样——`bulk run`
只是省去了逐条敲命令、逐条盯完成状态。结束后会打印一张汇总表：每株的退出码和
tier_r 分布，任何一株失败都不会让其它株的分析停下。

需要 HHpred/Foldseek 这类默认手动的证据时，见 `ADVANCED.md` 的批量合并小节——
不用给每一株单独跑一轮人工提交。

## 5. 读结果

`results/RUN_REPORT.md` 按这个顺序开场：

**Primary** — 直接受体结合证据最强的蛋白。**从这里开始做实验。**

**Rescue** — 证据弱于 Primary，但排除它们会带来不可接受的假阴性风险。

**Completeness** — 现有证据是否足以自信地压缩候选空间：

```
complete   没有发现样本特异的未解决风险，且审计覆盖完整
partial    存在局部、可界定的不确定性，短清单仍然是有意义的实验压缩
limited    风险大到短清单本身不可靠，不要把 Primary+Rescue 当作接近穷尽
```

同时报告 `assessment coverage`（这台机器能不能跑全部审计）——
**它是安装的属性，不是噬菌体的属性**，缺能力只会封顶，绝不单独制造 `limited`。

**Top unresolved candidates** — 只在 Primary 与 Rescue 都为空时出现。
这些蛋白**没有**被提升为 Rescue，展示它们是因为证据不足以形成可信清单，
它们也不计入任何召回指标。

**Provenance / limitations** — 实际用了哪些库、跳过了哪些以及为什么、
哪些尾部候选的序列证据已耗尽。

## 6. v1 不承诺什么

```
不承诺找出每一株的每一个真实 RBP
不承诺一个普适的固定清单上限
不承诺完全自动解释每一种尾部架构
```

已经实测到：有些 myovirus 的真实 RBP 在两个本地库都搜过之后仍然没有序列证据，
比较基因组也没有信号。对这类样本，正确的输出是 `completeness: limited` 与具体理由，
**而不是把清单扩大到看起来完整为止**。

## 7. 网络与数据库获取

自动获取会优先选择当前可用的传输方式。**网络或代理不允许时，它明确报错，
而已有数据库与核心 Mode A 分析完全不受影响**——你随时可以改用
`rbpfinder database configure` 注册一个已有的库。

「网络环境奇怪」不会变成整个 RBPfinder 安装失败。

### 7.1 在线 BLAST（`--stage2-backend ebi`）：会发生什么

不想在本机下载几百 MB 的序列库时，Stage 2 可以交给 EMBL-EBI 跑。提交前
RBPfinder 一定会先把这段话打给你看:

```
Remote Stage 2 uses EMBL-EBI.

The following data will be transmitted:
- candidate protein sequences
- contact email required for EBI job submission

RBPfinder will retrieve and parse the BLAST results automatically.
You will not need to return any result files manually.
```

**关于那个邮箱，最容易误会的一点:**

```
EBI contact email
Used only as contact information for the EMBL-EBI Job Dispatcher.
RBPfinder retrieves results automatically.
```

它**不是** RBPfinder 的登录账号,**结果也不会寄到这个邮箱**。它是 EBI Job
Dispatcher 对提交的任务要求的联系方式,仅此而已。BLAST 结果由 RBPfinder 自己
轮询、下载、解析——**你不需要等邮件,不需要去 EBI 网页上手工跑一遍,
也不需要把结果文件交回来。**

命令行上必须用 `--allow-remote` 明确授权;没有它就不会有任何一条序列离开这台机器:

```bash
rbpfinder --genbank x.gbk --out out/ --phage-id X   --stage2-backend ebi --data-policy remote_allowed --allow-remote   --ebi-email you@your-institution.example
```

`--allow-remote` 是授权,不是"别再提示我"——那段告知在授权的情况下照样打印,
并且连同你被告知的内容一起写进 `run_result.json` 的 provenance。

不给邮箱、或者不给 `--allow-remote`,运行会在**任何序列被发送之前**停下并说明原因。

## 8. 退出码

```
0  成功
2  输入错误（文件、manifest、配置）
3  能力缺失（缺 blastp、缺数据库……安装事实，不是缺陷）
1  未预期的内部错误（保留完整 traceback）
```
