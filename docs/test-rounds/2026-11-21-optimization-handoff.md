# RBPfinder 测试结果与优化任务交接文档

> 交接目的：交给优化 agent 执行工具与文档改进。
> 撰写日期：2026-11-21。撰写者：Biomni 测试会话（完整测试报告见 `report_rbpfinder_test.md`，本文为其可执行摘要）。

---

## 0. 对象与环境（必读）

| 项 | 值 |
|---|---|
| 工具 | rbpfinder v1.2.1+linux（Windows 版改造的 Linux 移植） |
| 源码 | https://github.com/Yarkev/RBPfinder，分支 `claude/loving-gauss-cl7oca`，commit **2cb9ed1** |
| 规则文件 | `decision_rules.yaml` v1.2.1+linux —— **任何情况下不得修改阈值** |
| WORKFLOW.md | 对应 commit 5913622；修订版已在 `/mnt/results/WORKFLOW.md` |
| 已修订 skill 包 | `/mnt/results/rbpfinder-biomni-skill-1.2.2linux.tar.gz` |
| 测试环境 | Biomni 沙盒 worker-0：rbpfinder 装在 `/workspace/.venv`（uv venv，editable，源码 `/workspace/rbpfinder_src`）；pharokka 1.10.1 + phold 1.2.5 前缀环境 `/workspace/env/pharokka`；foldseek 4f5c00cc 在 `/workspace/env/foldseek/bin/`；数据库：RefSeq 病毒蛋白 BLAST 库（722,823 条）`/workspace/dbs/blast/phage/phage_refseq`、PDB100 `/workspace/dbs/fs2/pdb100`、ProstT5 `/workspace/dbs/foldseek/prostt5/prostt5` |

**运行必需环境变量**（重启/休眠后 /workspace 保留，但需重设）：

```bash
export RBPFINDER_CONFIG=/workspace/dbs/rbpfinder_config.json
export RBPFINDER_FOLDSEEK=/workspace/env/foldseek/bin/foldseek
export RBPFINDER_FOLDSEEK_DB=pdb100=/workspace/dbs/fs2/pdb100
export RBPFINDER_PROSTT5_MODEL=/workspace/dbs/foldseek/prostt5/prostt5
```

**测试数据**：T7 `NC_001604`（金标准）、近缘队列 4 株（`/workspace/testdata/cohort/`）、用户真实基因组 `01F2-prokka.fa`（沙门氏菌噬菌体，53,260 bp 环状，birk/UPF_BP2/BP63 近缘群）。

**运行时产物**：`/mnt/results/rbpfinder_test_runs/`（含 `01F2_S_T/`、`t7_*` 各回归臂、`fullstack/` 全栈路线结果）。

---

## 1. 测试结论摘要

1. **196 个仓库测试全部通过**；14 项 WORKFLOW.md §5 已知问题全部核实（含源码级：#6 C6 未接线在 cli.py:588、#8 校准 pending、#13 远程死代码、#14 词表缺 receptor/lectin 术语）。
2. **T7 金标准回归**：证据阶梯行为符合文档；但 WORKFLOW.md 声称"配置 phage DB 使 gp17 R3→R2→Primary"**未复现**——S top-10 全为 "tail fiber protein" → apparatus 共识（10/10），gp17 不升。真正实现增益的是 phold accurate 路线：PHROG 4242 注释引入 D family → gp17 以 D+G+S（R2）进入 Primary。
3. **全栈路线**（pharokka → phold → rbpfinder `--genbank`）：T7 Primary = CDS_0039/0040 + gp17（所有路线最佳）；01F2 Primary 4 + Rescue 1，与 prokka 路线成员完全一致（phanotate vs prokka 起始密码子差异不影响候选集合）。架构自动判定 `B_myo_baseplate`（高置信度）。
4. **结构金标准对比**（用户冷冻电镜图谱，01F2）：工具 5 候选中 4 个获结构支持（1 个重定位）、1 个假阳性（gp16：注释 "tail fiber"，实为 tube initiator）。
5. **新缺陷**（本次发现，见 §2.1）：`--phold` TSV 模式静默丢失 T family 结构证据。

---

## 2. 工具优化任务清单（按优先级）

### P0-1：Mode B（`--phold` TSV）静默丢失 T family —— 修复

- **现象**：`--phold phold_per_cds_predictions.tsv` 时 `run_result.json` 中 `stage3_local.cds_searched: 0`，T family 证据全部缺失，**无任何告警**；01F2 上直接漏掉 2 个真候选（985 aa Fiber Ⅰ 大亚基、Ig 域 CDP）。
- **根因**：`rbpfinder/ingest/phold.py` 的 `read()` 构造的记录不含 `translation` 字段；`rbpfinder/stage3_local.py::prepare()` 用 `r.get("translation")` 过滤（约 L170：`recs = [r for r in cds_rows if r.get("translation") and ...]`），Mode B 下全部落空。
- **修复建议**（二选一，倾向 a）：
  - a) Mode B 必须同时提供 GenBank（校验 `phold_per_cds_predictions.tsv` 与 `phold.gbk` 同目录且 CDS 对齐），从中补读翻译序列；
  - b) 检测到 restrict_ids 非空但 0 条有序列时，`InputValidationError` 显式报错并说明原因与规避方法。
- **验收标准**：
  1. `--phold` TSV 模式下 stage3 不再出现静默 0 搜索（要么搜索成功要么显式失败）；
  2. 01F2 复跑结果与 `--genbank` 模式一致（Primary 4 + Rescue 1，成员相同）；
  3. 新增单测：TSV-only 输入、TSV+gbk 输入两条路径；
  4. 修改记录写入 `CHANGES-linux.md`（**不改 decision_rules.yaml**）。

### P0-2：注释词映射分歧导致假阳性（已知问题 #2 的量化）

- **现象**：01F2 gp16（258 aa）被 phold/PHROG 817 与 S-family 同源注释（BP63 直系同源 "tail fiber protein"，99.3% id）推入 Primary；冷冻电镜结构证实其为 **tube initiator**（基座组装组分，非受体结合）。
- **低成本改进**（不动阈值）：
  - D/S family 命中时同时记录 foldseek/BLAST 命中描述；当结构命中描述含 baseplate/complex/assembly 类词汇而产品词为 tail fiber 时，输出一致性提示（不自动降级，供人工判读）；
  - 新增基因组邻接加分信号：候选下游紧邻 ≥1 个 tail fiber assembly protein（伴侣）→ 输出为"纤维佐证"（gp23 满足，gp16 不满足）。注意这只能进提名/展示层，**不能进 family 计数**（G family 的 C1 位置证据已是独立 family，勿重复计数）。
- **验收标准**：01F2 复跑输出对 gp16 显示结构-注释一致性提示与伴侣信号缺失；T7 回归不回归（Primary 仍含 0039/0040/0052）。

### P1-1：`--vocabulary unified` 回归缺口

- T7 phold 路线上 `--vocabulary unified` 把 gp17 挤出 Primary（R3-E3），生物学错误；`current` 与 `explicit_guard` 正确。这是该决策目前唯一数据点。
- **任务**：补 48-strain（或可获得的近缘集）vocabulary 回归，产出三种模式的提名差异表，再决定默认值。**在此之前不要改默认。**

### P1-2：run_result.json 条件键

- `top_unresolved_candidates` 仅在 shortlist 空或 completeness=limited 时输出（cli.py:774 附近），顶层键数 31/32 不定。
- **建议**：文档注明；或改为恒输出 + `null`。属执行契约小改，记 `CHANGES-linux.md`。

### P2-1：上游反馈（非本仓库，可选）

- pharokka 1.10.1 `input_commands.py` MMseqs2 版本白名单缺 v19 → `UnboundLocalError`（已打最小补丁：else 分支解析实际版本字符串；补丁内容见 §4）。
- pharokka `install` 将 Zenodo 403（封锁页）误报为 MD5 不符。
- 建议向上游 gbouras13/pharokka 提 issue。

---

## 3. 文档修订任务（WORKFLOW.md / SKILL.md / KNOW_HOW.md）

1. **修正未复现声明**：WORKFLOW.md 中"配置 phage DB 使 gp17 R3→R2→Primary"改为实测机制（见 §1.2）。
2. **新增"结构证据优先"小节**：有冷冻电镜/高置信预测结构时，结构定位 > 注释转移；引用 01F2 案例（4/5 支持、1 重定位、1 假阳性）。
3. **明确推荐 `--genbank phold.gbk` 而非 `--phold` TSV**（直至 P0-1 修复）。
4. **推荐全栈路线为默认**：pharokka 架构 typing（B_myo_baseplate 自动高置信）+ mash 近缘报告为额外收益，候选集合与 prokka 路线一致。
5. **Troubleshooting 新增**（全部实测踩坑）：
   - foldseek 静态二进制：`https://mmseqs.com/foldseek/foldseek-linux-avx2.tar.gz`（**mmseqs.org 会 302 到仓库页**）；`databases PDB <out> <tmp>` 需 3 个路径参数；输出前缀不得含目录成分（从目标目录内用平铺前缀）。
   - phold 不在 bioconda：`pip install phold==1.2.5`（PyPI 无 1.2.6），需同时固定 `numpy<2`、`transformers==4.46.3`；混装损坏表现为 `np.long` AttributeError（scipy 源文件混入旧版，`pip install --force-reinstall --no-deps scipy==1.14.1` 修复）。
   - pharokka DB 手动安装：解压 `pharokka_v1.8.0_databases.tar.gz` 后将内层目录内容摊平到 `<env>/lib/python3.13/databases/`，用 `check_db_installation()` 验证（Zenodo 被封时的替代路径）。
   - blastp 参数为 `-num_threads`（非 `-threads`，报 USAGE 即参数名错）。
   - /opt/conda 与 apt 装的包在机器休眠后丢失；一切前缀环境放 /workspace。
6. run_result.json 消费提示：`top_unresolved_candidates` 条件输出（P1-2）。

---

## 4. 可复现命令（回归基准）

```bash
# 全栈注释（~2 分钟/基因组 ×2）
pharokka run -i 01F2-prokka.fa -o runs/pharokka_01F2 -d $DBDIR -t 8 -l 01F2 -f
phold run -i runs/pharokka_01F2/pharokka.gbk -o runs/phold_01F2_fullstack -t 8 -f   # 需 foldseek 在 PATH

# RBPfinder（--genbank 模式）
rbpfinder --genbank runs/phold_01F2_fullstack/phold.gbk --out runs/01F2_fullstack_gb \
  --phage-id 01F2 --circular

# T7 同理（无 --circular）
```

**回归判定**（改动后必须逐条核对）：
- T7 全栈：Primary = {CDS_0039, CDS_0040, CDS_0052}；
- 01F2 全栈：Primary = {CDS_0010, CDS_0011, CDS_0012, CDS_0019}，Rescue = {CDS_0026}（pharokka 编号；prokka 编号对应 CDS_0008/0009/0010/0016 + CDS_0023）；
- 01F2 架构判定 B_myo_baseplate（high）；T7 为 E_jumbo_or_unresolved（low）；
- 196 个仓库测试通过。

---

## 5. 结构金标准数据（01F2，来自用户冷冻电镜图谱）

面板 b 基因框颜色与图例做了矢量级匹配，基因编号 = CDS 编号（跳过 gp3/gp14）：

| gp | CDS (prokka) | 身份 | RBP 提名 | 结构结论 |
|---|---|---|---|---|
| gp8 | CDS_0008 | Fiber Ⅰ 亚基 1（浅薰衣草） | Primary | ✅ 真候选 |
| gp9 | CDS_0009 | Fiber Ⅰ 主轴 986 aa，Lic26A Family 11 糖结合模块 | Primary | ✅✅ 头号 RBP |
| gp10 | CDS_0010 | CDP 衣壳装饰蛋白（Ig-1/Ig-2，AF3 模型确认） | Primary | ⚠ 重定位：非尾部 RBP，但糖结合能力值得独立测试 |
| gp16 | CDS_0016 | tube initiator（亮绿，精确匹配） | Primary | ❌ 假阳性 |
| gp23 | CDS_0023 | Fiber Ⅱ（基座远端纤维，下游 gp24/25 伴侣佐证） | Rescue | ✅ 真候选 |

---

## 6. 约束与红线（给优化 agent）

1. **不得修改 `decision_rules.yaml` 的任何阈值**——阈值改动需单独的基准决策；运行参数类改动只能走 `Rules.set_override`（如 `vocabulary.mode`）。
2. 执行契约类修复（CLI/IO/schema）记录进 `CHANGES-linux.md`；不要混入规则变更。
3. 每项工具改动后重跑 §4 回归基准；196 个单测必须保持通过。
4. 提名/展示层的新信号（P0-2）不得改变 family 计数逻辑。
5. 产物位置：修订文档与 skill 包更新后仍放 `/mnt/results/`，版本号递增（skill 1.2.2 → 1.2.3）。
