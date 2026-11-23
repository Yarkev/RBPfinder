// Build the editable Chinese .docx user guide for RBPfinder (Biomni / Linux).
//
//     node packaging/biomni/make_guide_docx.js 使用指南.docx
//
// Same material as GETTING-STARTED.zh.md and make_guide_pdf.py, as a Word file someone
// can edit. Real Heading1/2 styles WITH outlineLevel, so the navigation pane, the
// outline view and a generated table of contents all work -- overriding `paragraph` in
// a style replaces the built-in properties wholesale, and leaving outlineLevel out
// produces headings that look right and are invisible to all three.
//
// Needs the `docx` npm package. Update the version strings when the tool version moves.
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
} = require("docx");

const HAN = "微软雅黑";          // present on Windows; Word substitutes sensibly elsewhere
const MONO = "Consolas";        // ASCII-only blocks, where column alignment matters

const ACCENT = "1D5C8A";
const CODEBG = "F4F5F7";
const OKBG = "EEF5EE";
const WARNBG = "FDF4E3";
const RULE = "D8D8D8";

const PAGE_W = 9026;            // A4 usable width in DXA at 2.2cm margins

const NONE = { style: BorderStyle.NONE, size: 0, color: "auto" };
const noBorders = { top: NONE, bottom: NONE, left: NONE, right: NONE };

function p(text, opts = {}) {
  const { bold, size = 21, color = "1A1A1A", after = 120, before = 0, font = HAN } = opts;
  const runs = Array.isArray(text) ? text : [{ t: text }];
  return new Paragraph({
    spacing: { after, before, line: 300 },
    children: runs.map((r) => new TextRun({
      text: r.t, bold: r.b !== undefined ? r.b : bold, font,
      size: r.size || size, color: r.color || color,
    })),
  });
}

function h(text, level) {
  return new Paragraph({ text, heading: level });
}

// A shaded block. `mono` for pre-formatted ASCII where alignment carries meaning;
// Chinese prompt text uses the body font, since it has no columns to keep.
function block(lines, { mono = false, bg = CODEBG } = {}) {
  const rows = lines.map((line) => new TableRow({
    children: [new TableCell({
      width: { size: PAGE_W, type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: bg, color: "auto" },
      margins: { top: 20, bottom: 20, left: 160, right: 160 },
      borders: noBorders,
      children: [new Paragraph({
        spacing: { after: 0, line: 260 },
        children: [new TextRun({
          text: line || " ", font: mono ? MONO : HAN, size: mono ? 17 : 19,
          color: "1A1A1A",
        })],
      })],
    })],
  }));
  return new Table({
    columnWidths: [PAGE_W], width: { size: PAGE_W, type: WidthType.DXA },
    borders: noBorders, rows,
  });
}

function table(header, rows, weights) {
  const total = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map((w) => Math.round((w / total) * PAGE_W));
  const cell = (txt, { head = false, w } = {}) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: head
      ? { type: ShadingType.CLEAR, fill: ACCENT, color: "auto" }
      : undefined,
    margins: { top: 60, bottom: 60, left: 110, right: 110 },
    children: [new Paragraph({
      spacing: { after: 0, line: 280 },
      children: parseBold(txt).map((seg) => new TextRun({
        text: seg.t, bold: head || seg.b, font: HAN, size: 19,
        color: head ? "FFFFFF" : "1A1A1A",
      })),
    })],
  });
  const body = [new TableRow({
    tableHeader: true,
    children: header.map((txt, i) => cell(txt, { head: true, w: widths[i] })),
  })];
  rows.forEach((r) => body.push(new TableRow({
    children: r.map((txt, i) => cell(txt, { w: widths[i] })),
  })));
  const line = { style: BorderStyle.SINGLE, size: 3, color: RULE };
  return new Table({
    columnWidths: widths, width: { size: PAGE_W, type: WidthType.DXA },
    borders: { top: line, bottom: line, left: line, right: line,
               insideHorizontal: line, insideVertical: line },
    rows: body,
  });
}

// **bold** -> runs, so table text can emphasise without hand-building runs each time
function parseBold(s) {
  const out = [];
  s.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**")) out.push({ t: part.slice(2, -2), b: true });
    else out.push({ t: part, b: false });
  });
  return out.length ? out : [{ t: s, b: false }];
}

const gap = (n = 160) => new Paragraph({ spacing: { after: n }, children: [] });

// ------------------------------------------------------------------ content

const C = [];

C.push(new Paragraph({ text: "RBPfinder 使用指南", heading: HeadingLevel.TITLE }));
C.push(p([{ t: "Biomni / Linux 版  ·  v1.2.8+linux  ·  2026-11-23" }],
         { size: 19, color: "5C5C5C", after: 40 }));
C.push(p("从噬菌体基因组找出受体结合蛋白（RBP）候选",
         { size: 19, color: "5C5C5C", after: 240 }));

C.push(h("1. 它做什么", HeadingLevel.HEADING_1));
C.push(p("给它一个已注释的噬菌体基因组，它输出一份按证据分层的受体结合蛋白候选清单：先测哪几个、"
         + "哪几个是为保召回留着的、以及这份清单有多完整。"));
C.push(table(["边界", "说明"], [
  ["**不做注释**",
   "注释是 Biomni 的活（pharokka / phold）。这是刻意分工 —— 如果它自己注释，注释工具升级"
   + "就能悄悄改变分层结果，而没人会察觉。"],
  ["**不联网**",
   "所有远程提交路径都被拒绝（退出码 2，在读取任何序列之前）。序列不离开这台机器，"
   + "没有开关可以打开。"],
  ["**不保证找全**",
   "证据不足时它输出 completeness: limited 并说明原因，而不是凑一份好看的清单。"
   + "**这个状态是结果的一部分，必须读。**"],
  ["**适用范围**",
   "有尾双链 DNA 噬菌体（Caudoviricetes）。RNA / 单链 DNA / 丝状 / 无尾 / 古菌病毒"
   + "不在验证范围内。"],
], [16, 84]));
C.push(gap());

C.push(h("2. 安装", HeadingLevel.HEADING_1));
C.push(p("先判断你是哪种情况 —— 三种的指令完全不同。把对应的那段话直接发给 Biomni。"));

C.push(h("情况 A：全新沙箱", HeadingLevel.HEADING_2));
C.push(block([
  "https://github.com/Yarkev/RBPfinder 这是一个 skill，装到这台沙箱上。",
  "克隆后读 packaging/biomni/README.md 按步骤做，特别注意这三节：",
  " - \"Copying the files is NOT registration\"",
  " - \"When the Foldseek CDN refuses you\"",
  " - \"2a. Verify the deployment\"",
]));
C.push(p("耗时主要在下载：结构库约 6.5 GB、ProstT5 权重约 2.3 GB。",
         { size: 18, color: "5C5C5C", before: 100 }));

C.push(h("情况 B：沙箱已配好，只更新版本", HeadingLevel.HEADING_2));
C.push(block([
  "把 RBPfinder 更新到最新版并重新部署 skill。",
  "",
  "cd /workspace/rbpfinder_src && git fetch origin && git checkout main",
  "source /workspace/rbpfinder.env",
  "uv pip install --force-reinstall --no-deps -e /workspace/rbpfinder_src",
  "",
  "然后重新部署 skill 包，过 check_skill，并走平台注册流程生成更新卡片。",
  "最后跑一次任意基因组，报告 run_result.json 的 versions 字段。",
]));

C.push(h("情况 C：机器被重建了（OOM / delete）", HeadingLevel.HEADING_2));
C.push(block([
  "这台机器被重建了，恢复 RBPfinder 环境。",
  "/shared 上有结构库和 pharokka 库的 tarball 备份，用恢复脚本从那里恢复 ——",
  "不要去跑 foldseek databases，那个 CDN 对本机 IP 限流。",
  "然后 checkout main、重装、重新部署并注册 skill，最后 rbpfinder doctor。",
]));
C.push(gap(120));

C.push(table(["安装时有两个坑，每次都会遇到", ""], [
  ["**① 复制文件不等于注册**",
   "把 SKILL.md 复制到位、check_skill 通过 —— 技能栏里还是看不到它。注册是独立的平台流程："
   + "agent 生成一张预览卡片，**你必须确认保存**，之后才会出现在技能栏。"],
  ["**② 更新时 --force-reinstall 不能省**",
   "可编辑安装下 git checkout 只换文件、不换安装元数据，所以运行会报出一个过期的版本号。"],
], [30, 70]));
C.push(gap());

C.push(h("3. 验证装好了（别跳过）", HeadingLevel.HEADING_1));
C.push(block(["rbpfinder doctor"], { mono: true }));
C.push(p("四项核心应该是 AVAILABLE：rules、schema、Local Stage 2、Local Stage 3。"
         + "然后跑一次任意基因组，看这个字段：", { before: 120 }));
C.push(block(["python -c \"import json; print(json.load(",
              "    open('<输出目录>/run_result.json'))['versions'])\""], { mono: true }));
C.push(gap(120));
C.push(table(["看到", "意思", "做什么"], [
  ["consistency: ok", "装对了", "继续"],
  ["consistency: package_metadata_stale", "代码是新的，安装元数据是旧的", "按它 note 里的话重装"],
  ["consistency: not_installed", "根本没装上", "重装"],
], [34, 38, 28]));
C.push(p("这一步重要是因为：曾经有一整轮测试跑在旧代码上并被报告为通过 —— 一个非可编辑副本遮蔽了"
         + "可编辑路径，git checkout 对实际运行的东西毫无影响，而输出里没有任何迹象。",
         { size: 18, color: "5C5C5C", before: 120 }));
C.push(gap());

C.push(h("4. 怎么用", HeadingLevel.HEADING_1));
C.push(p("不需要特殊指令，用自然语言说你要做什么。最短的形式："));
C.push(block(["找出 /data/01F2.fasta 里的受体结合蛋白候选。宿主是 Salmonella。"]));
C.push(p("但下面三件事值得每次都说 —— 它们会改变结果：", { before: 140 }));
C.push(block([
  "用 RBPfinder 分析 /data/01F2.fasta。",
  "宿主 Salmonella。",
  "走 accurate 路线（跑 phold）。",
  "基因组是环状。",
]));
C.push(gap(120));
C.push(table(["说什么", "不说会怎样"], [
  ["**fast 还是 accurate**",
   "skill 会主动问你，说了省一轮。这个差别比听起来大 —— 在 T7 上它就是文献确立的 RBP "
   + "在不在清单里的区别。"],
  ["**宿主属名**",
   "不会出错，但少一层证据。只在配了对应宿主库且覆盖该菌属时才用得上，否则记录并跳过。"],
  ["**是否环状**",
   "**最容易悄悄吃亏的一条。**GenBank 没声明 topology 时必须显式说。尾模块跨过原点时提名会丢，"
   + "**而候选池大小不变**（别的通道补上了同一个基因），所以没有任何打印出来的数字会暴露"
   + "这个退化。"],
], [22, 78]));
C.push(gap());

C.push(h("fast 和 accurate 怎么选", HeadingLevel.HEADING_2));
C.push(table(["", "fast（默认）", "accurate"], [
  ["注释", "pharokka", "pharokka + phold（全基因组）"],
  ["额外耗时", "—", "8 核上 1–2 小时"],
  ["买到什么", "—", "更好的注释：解决 pharokka 留作 hypothetical 的基因"],
], [15, 28, 57]));
C.push(p("实测在 T7（文献金标准）上：", { before: 140 }));
C.push(block([
  "fast      Primary: CDS_0039, CDS_0040              gp17 不在清单里",
  "accurate  Primary: CDS_0039, CDS_0040, CDS_0052    gp17 在清单里（它就是文献 RBP）",
], { bg: OKBG }));
C.push(p([{ t: "注释差的基因组选 accurate。", b: true },
          { t: "一小时 CPU 对一份错的清单来说很便宜。" }], { before: 140 }));
C.push(gap());

C.push(h("5. 会输出什么", HeadingLevel.HEADING_1));
C.push(p("跑完终端会打印一行摘要，并在输出目录写下一组文件。摘要长这样："));
C.push(block([
  "OK   primary 4 | rescue 0 | completeness partial | coverage reduced",
  "     internal pool 12 of 12 CDS (recall protection, not the shortlist)",
  "     limitation [capability_unavailable]: completeness audit A2 could not run",
  "     schema: PASS   outputs: runs/01F2/",
], { mono: true, bg: OKBG }));
C.push(gap(140));

C.push(h("输出目录里的文件", HeadingLevel.HEADING_2));
C.push(table(["文件", "是什么", "要看吗"], [
  ["RUN_REPORT.md", "人读的报告：候选清单、完整性、证据追溯、审计记录", "**先看这个**"],
  ["RBP_candidates.tsv", "候选表格，可直接导入 Excel", "**要看**"],
  ["run_result.json", "机器读的完整结果，所有字段都在这里", "追问细节时看"],
  ["all_CDS_screening.tsv", "全部基因的筛查记录 —— 每个基因为什么在或不在池子里", "排查时看"],
  ["*_BATCH.tsv", "需要人工去外部服务跑的批次文件（HHpred / AlphaFold 等）", "想进一步确证时看"],
], [26, 52, 22]));
C.push(gap());

C.push(h("报告的三层结构 —— 按这个顺序读", HeadingLevel.HEADING_2));
C.push(table(["层", "含义", "怎么用"], [
  ["**Primary**", "证据最强，支持直接的受体结合作用", "**优先去测这些**"],
  ["**Rescue**", "留着是为了保召回，不是因为证据强", "预算允许时再测"],
  ["**Completeness**", "complete / partial / limited，连同它给的原因",
   "**必须读** —— 它说明这份清单有多可信"],
], [18, 50, 32]));
C.push(gap(120));
C.push(table(["内部候选池不是实验清单"], [
  ["报告里还有一个「internal pool」，可能有十几二十个基因。它是为算法设计的**召回保护装置**，"
   + "不是要去测的清单。把它当清单，前面所有分层工作就白做了。"],
], [100]));
C.push(gap());

C.push(h("候选表格长什么样", HeadingLevel.HEADING_2));
C.push(block([
  "rank  cds_id         tier   families  set_membership  annotation",
  "1     DEMO_CDS_0004  R2-E2  D,G       primary         tail fiber protein",
  "2     DEMO_CDS_0005  R2-E2  D,G       primary         tailspike protein",
  "3     DEMO_CDS_0006  R2-E2  D,G       primary         tip attachment protein",
  "4     DEMO_CDS_0007  R2-E2  D,G       primary         tail sheath and receptor...",
  "5     DEMO_CDS_0000  R3-E2  D,G       reported_only   tail sheath protein",
], { mono: true }));
C.push(gap(140));

C.push(h("分层怎么读：两个轴", HeadingLevel.HEADING_2));
C.push(p([{ t: "分层是两个独立的轴。" },
          { t: "tier_r 是「它是什么」，tier_e 是「我们看得多仔细」。", b: true },
          { t: "两者分开，所以一次证据不足的运行不会被误读成一个有信心的否定结论。" }]));
C.push(table(["tier_r — 它是什么", "tier_e — 看得多仔细"], [
  ["**R1**  强 RBP：≥2 个受体结合家族，且含一个直接家族", "**E3**  ≥3 个独立证据家族"],
  ["**R2**  可能是 RBP / 召回保护", "**E2**  2 个"],
  ["**R3**  属于受体结合装置，但没有直接的识别证据", "**E1**  1 个，或未解决"],
  ["**N**  有正面证据表明它不是 RBP", ""],
], [60, 40]));
C.push(p("所以 R2-E2 读作「可能是 RBP，有两条独立证据」；R3-E1 读作「属于尾部装置，而且我们"
         + "只看到一条证据」—— 后者的低分层很大程度上是因为看得不够。",
         { size: 18, color: "5C5C5C", before: 120 }));
C.push(gap());

C.push(h("完整性那一段长什么样", HeadingLevel.HEADING_2));
C.push(block([
  "completeness assessment    partial  -- sample-specific unresolved RBP risk",
  "assessment coverage        reduced  -- 可选审计在这台机器上能否执行",
  "sample-specific            complete -- 因本机无法执行全部审计而被封顶为 partial",
  "",
  "Limitations bounding this result:",
  "- [installation] completeness audit A2 could not run: missing genomewide T or M",
  "- [note] comparative analysis had no informative cohort",
], { mono: true }));
C.push(p([{ t: "注意最后两行的区别：" }, { t: "[installation]", b: true },
          { t: " 是你这台机器缺能力（可以补），" }, { t: "[note]", b: true },
          { t: " 是这次数据本身不适用（无需处理）。" }],
         { size: 18, color: "5C5C5C", before: 120 }));
C.push(gap());

C.push(h("Primary 是空的怎么办", HeadingLevel.HEADING_2));
C.push(p([{ t: "空不等于「这里没有 RBP」。", b: true },
          { t: "先看哪些证据家族根本没跑起来：" }]));
C.push(block([
  "stage2_local    配了序列数据库吗？",
  "stage3_local    本地结构搜索跑了吗，搜了多大范围？",
  "limitations     哪些审计报告自己无法执行？",
], { mono: true }));
C.push(p("然后判断是哪一种：证据饥饿（某些家族从来没跑，所以没东西能到达进 Primary 的层级）"
         + "还是基因组本身确实没解决（全都跑了还是没有合格的）。把没跑起来的家族和结果一起报告 ——"
         + "不要把一次饥饿的运行当成生物学发现。", { before: 140 }));
C.push(gap());

C.push(h("拿到结果后值得追问的两句", HeadingLevel.HEADING_2));
C.push(block([
  "versions 和 vocabulary 字段是什么？",
  "presentation_signals 里有没有触发？",
]));
C.push(p("前者决定这批数字以后还能不能和别的批次比；后者是候选校正提示 —— 部件名假阳性、"
         + "伴侣基因邻接、证据方向冲突。", { size: 18, color: "5C5C5C", before: 120 }));
C.push(gap());

C.push(h("6. 常见问题", HeadingLevel.HEADING_1));
C.push(table(["看到什么", "怎么办"], [
  ["probability_pct 全是 null",
   "**预期行为。**这条路线上序列没有骨架坐标，算不出概率。什么都不用做，别重试。"],
  ["A2 could not run: missing_genomewide_T_or_M",
   "**默认路线的设计行为，不是故障。**说明完整性上限即可。"],
  ["Local Stage 3 UNAVAILABLE", "结构库、ProstT5 权重或 foldseek 缺一个。看 README 第 1 节。"],
  ["stage3_local.cds_searched: 0",
   "用了 phold 的 TSV 作输入 —— 那个表格没有蛋白序列列。改用 --genbank phold.gbk。"],
  ["foldseek 下载报 429",
   "CDN 对数据中心 IP 限流。README「When the Foldseek CDN refuses you」有四条替代路径。"],
  ["pharokka install 报 MD5 不符",
   "实际是下到了几百字节的拦截页。**先看文件大小**，再走手动安装。"],
  ["昨天还在的工具不见了", "/opt/conda 和 apt 装的包不过休眠。所有环境放 /workspace。"],
  ["结果像是旧代码跑的", "非可编辑副本遮蔽了可编辑路径。见本指南第 3 节。"],
], [36, 64]));
C.push(gap());

C.push(h("7. 不该做的事", HeadingLevel.HEADING_1));
C.push(table(["不该做的事", "为什么"], [
  ["**不要改阈值来让结果好看**", "一个为了迁就答案而改动的阈值，不再是任何东西的证据。"],
  ["**不要让 agent 自己重新排名**",
   "如果它说「虽然工具给了 R3，但我认为这个更可能是 RBP」，它在用工具的权威包装一个猜测。"
   + "它应该做的是：指出分歧、解释机制、说清它没有改变工具的结论。**指出分歧不是重新排名。**"],
  ["**不要手工建结构库**",
   "能建成，但没有 taxonomy 文件，搜索会在运行时死掉。doctor 把这种库报成 "
   + "CONFIGURED, INVALID 是刻意的。"],
  ["**不要用 phold 的 TSV 作输入然后期待结构证据**", "那个表格没有蛋白序列列。用 phold.gbk。"],
], [32, 68]));
C.push(gap());

C.push(p("完整参考：仓库 packaging/biomni/README.md（部署细节）、"
         + "packaging/biomni/GETTING-STARTED.zh.md（本指南的 markdown 版，随代码更新）、"
         + "WORKFLOW.md §5（错答案可能来自哪里，共 16 条已知弱点）。",
         { size: 18, color: "5C5C5C", after: 60 }));
C.push(p("本文档对应 v1.2.8+linux；代码更新后以仓库内文档为准。",
         { size: 18, color: "5C5C5C" }));

// ------------------------------------------------------------------ document

const headingRun = (size) => ({ font: HAN, size, bold: true, color: "1D5C8A" });

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: HAN, size: 21, color: "1A1A1A" },
                  paragraph: { spacing: { line: 300 } } },
      // outlineLevel is what the navigation pane, the outline view and a generated
      // table of contents read. Overriding `paragraph` here replaces the built-in
      // style's properties wholesale, so omitting it silently produced headings that
      // looked right and were invisible to every one of those -- which matters, since
      // this document exists to be edited.
      title: { run: { font: HAN, size: 40, bold: true, color: "1A1A1A" },
               paragraph: { spacing: { before: 0, after: 80 }, keepNext: true } },
      heading1: { run: headingRun(30),
                  paragraph: { spacing: { before: 320, after: 140 }, keepNext: true,
                               outlineLevel: 0 } },
      heading2: { run: { font: HAN, size: 23, bold: true, color: "1A1A1A" },
                  paragraph: { spacing: { before: 220, after: 100 }, keepNext: true,
                               outlineLevel: 1 } },
      heading3: { run: { font: HAN, size: 21, bold: true, color: "1A1A1A" },
                  paragraph: { spacing: { before: 180, after: 80 }, keepNext: true,
                               outlineLevel: 2 } },
    },
  },
  sections: [{
    properties: { page: { margin: { top: 1134, bottom: 1134, left: 1247, right: 1247 } } },
    footers: {},
    children: C,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(process.argv[2] || "guide.docx", buf);
  console.log("wrote", process.argv[2] || "guide.docx");
});
