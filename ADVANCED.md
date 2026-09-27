# RBPfinder — 进阶用法

普通用户不需要本文。正式路径见 `USER_GUIDE.md`。

---

## 1. Replay：用已有的 Stage 2 产物重跑

```bash
rbpfinder --genbank phage.gbk --phage-id MyPhage01 \
          --stage2-manifest results/stage2_local/stage2_manifest.tsv \
          --out replay/
```

`--stage2-manifest` 是**权威回放输入**：给了它就不跑 BLAST，
**也完全不咨询已配置的数据库**。用途是逐字节复现一次历史运行。

每次自动运行都会在 `results/stage2_local/` 留下可回放的产物：
每个 CDS 一个 outfmt-6 文件（**没有命中就是 0 字节文件，不是缺文件**）加一份 manifest。
"查过、没查到"与"根本没查"是两种状态，靠这个区分。

manifest 是三列 TSV（`cds_id`/`kind`/`path`），可以手写。约束：

```
UTF-8 BOM            支持（Windows 工具默认会写）
缺必需列              exit 2，报出文件名与缺的列名
引用不存在的产物文件   exit 2，报出行号与路径 —— 绝不当作 0 命中
无该 CDS 的条目       该 CDS 视为 provider 未运行（not_run）
```

## 1b. 批量合并手动证据轮次（`bulk merge-batches` / `bulk apply-returns`）

`USER_GUIDE.md` 的 `bulk run` 能一次跑完几十株基因组，但 HHpred / Foldseek 这类
默认手动提交的证据（见 `DATA_POLICY.md`）不会自动补齐——每一株用完自动化能给的
证据后，仍然可能剩下几个候选需要人工提交。逐株做这件事就是几十轮手动流程；
合并之后只做一轮：

```bash
# 1. 批量跑完，自动化能做的（本地 BLAST、EBI 远程 InterProScan 若已授权）都已完成
rbpfinder bulk run --manifest genomes.tsv --out-root results/

# 2. 把所有株仍缺 HHpred 的候选合并成一份 batch
rbpfinder bulk merge-batches --manifest genomes.tsv --out-root results/ \
          --provider hhpred
#   写出 results/MERGED_HHPRED_BATCH.tsv         （所有株的候选序列，一份文件）
#   写出 results/HHPRED_RETURN_MANIFEST.tsv      （提交完在这里填 path 列）

# 3. 提交一次、拿到结果、填好 path 列之后，一次性分发回每一株
rbpfinder bulk apply-returns --manifest genomes.tsv --out-root results/ \
          --stage2-manifest results/HHPRED_RETURN_MANIFEST.tsv
```

`apply-returns` 对清单里的**每一株**都重跑一次（`--stage2-manifest` 指向同一份合并
文件），不需要先把结果拆回各株——每株只会认领属于自己的行：`cds_id` 本来就带
phage_id 前缀，不是自己的候选会被安全拒绝并在日志里报出原因
（`no candidate 'X' in this run`），绝不会张冠李戴到别的株上。这一步不加
`--restart-stage2`：续跑是默认行为，`apply-returns` 补的正是"缺什么就只问什么"。

`--provider` 可以是 `hhpred`、`foldseek`、`interproscan`、`alphafold`——机制完全一样，
只是 AlphaFold 的产物要先跑一次 Foldseek 才能作为证据导回来（见
`evidence_acquisition.providers.alphafold.return_via`）。

不合适用这条路的情形：只有一两株、或者本来就打算逐株交互式确认——那种情况用
skill 的 `references/INTERACTIVE_CONFIRMATION.md` 流程即可，不必先跑 `bulk`。

## 2. 显式指定数据库与可执行文件

优先级是 **authority，不是搜索顺序**：显式给的东西无效就失败，
**绝不悄悄回落到下一层**。

```
数据库    显式 CLI  >  RBPFINDER_DB_<ROLE>  >  持久配置  >  未配置
可执行    --blastp / --makeblastdb  >  RBPFINDER_BLASTP / RBPFINDER_MAKEBLASTDB  >  PATH  >  unavailable
```

```bash
RBPFINDER_DB_HOST=/lab/share/uniprot_klebsiella rbpfinder --genbank ... --host "Klebsiella pneumoniae" ...
rbpfinder --genbank ... --blastp /opt/ncbi/bin/blastp ...
```

env 指向一个坏路径时，即使持久配置里有一个好库，也**报 env 这一层失败**——
静默绕过去会让人以为用的是自己配置的那个库。

## 3. host-context 与 capability 的实际行为

`--host` 只影响 DB_HOST 是否被使用；DB_PHAGE 对任何有尾噬菌体都适用。

```
匹配      DB_PHAGE + DB_HOST
未提供    DB_PHAGE only，记录 skipped_due_to_host_context
不匹配    DB_PHAGE only，记录两边的组织名
```

匹配是**属级包含**，大小写无关：`Stenotrophomonas maltophilia` 匹配 Stenotrophomonas 库，
`Klebsiella pneumoniae` 不匹配。判不出来一律算不匹配——
用错宿主库是科学错误，跳过只是一条被记录的限制。

`rbpfinder doctor` 会显示宿主库的 `host scope`，但它**没有 run context**，
不判断是否适用于你手上的样本；运行时的 `--host` 门控才是最终 authority。

跳过本地 Stage 2（fixture、快速冒烟、复现实验）：

```bash
rbpfinder --genbank ... --no-local-stage2 ...
```

## 4. C5 比较基因组

```bash
rbpfinder --genbank phage.gbk --phage-id X --comparative-cohort /path/to/related_genomes/ --out results/
```

比较对象按 **shared gene content** 自动挑选，与 RBP 标签无关。
没有合适 cohort 时 C5 报 `implemented_but_no_comparator_data`——
这是**已解决**状态（能力在、没有可比对象），不降低 completeness。

C5 的证据固定为 `supports_apparatus_membership`，
**永远不会**自行升级成 `supports_receptor_binding`：
"在近缘株中快速演化"不等于"结合受体"。

## 5. 数据获取

```bash
rbpfinder database acquire phage --method auto|direct|stream|cursor
```

`--method X` 是 authority：跑不了就失败，不会自动换一种。
`auto` 按冻结层级尝试，且**只在 transport 不可用时**下移；
**完整性失败一律硬失败**，绝不换条路再取一遍。

已配置且有效的库 → 直接复用，零网络、零重建。

> acquisition 的两条真实网络实证已于 2026-08-25 通过（`M15C_NETWORK_SMOKE=PASS`）。
> 仍未做真实网络验证的是 M16 远程 Stage 2 backend，见 `KNOWN_ISSUES.md` 第 3 节。
