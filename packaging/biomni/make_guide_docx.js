// Build the editable Chinese .docx user guide for RBPfinder (Biomni / Linux).
//
//     node packaging/biomni/make_guide_docx.js 使用指南.docx [图片目录]
//
// Structure and tone follow the maintainer's own rewrite (2026-11-25): get the reader
// running first, give copy-pasteable prompts rather than commands, explain the jargon
// instead of assuming it. The evidence-family section exists because users asked what
// the letters mean -- they appear in every report and nothing had ever spelled them out.
//
// If an image directory is given it should hold image1..image4.png: the flowchart and
// the three platform screenshots from the maintainer's edit. Without it the figures are
// replaced by a note, so the document still builds.
//
// Needs the `docx` npm package. Update the version strings when the tool version moves.
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, ImageRun, PageBreak,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle, AlignmentType,
  LineRuleType,
} = require("docx");

const HAN = "微软雅黑";
const MONO = "Consolas";
const ACCENT = "1D5C8A";
const CODEBG = "F4F5F7";
const OKBG = "EEF5EE";
const WARNBG = "FDF4E3";
const RULE = "D8D8D8";
const PAGE_W = 9026;

const IMGDIR = process.argv[3] || path.join(__dirname, "guide-images");

const NONE = { style: BorderStyle.NONE, size: 0, color: "auto" };
const noBorders = { top: NONE, bottom: NONE, left: NONE, right: NONE };

function bold(s) {
  const out = [];
  s.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**")) out.push({ t: part.slice(2, -2), b: true });
    else out.push({ t: part, b: false });
  });
  return out.length ? out : [{ t: s }];
}

function p(text, opts = {}) {
  const { size = 21, color = "1A1A1A", after = 120, before = 0, font = HAN } = opts;
  return new Paragraph({
    spacing: { after, before, line: 320 },
    children: bold(text).map((r) => new TextRun({
      text: r.t, bold: r.b, font, size, color,
    })),
  });
}

const note = (t) => p(t, { size: 18, color: "5C5C5C", before: 60 });
const gap = (n = 160) => new Paragraph({ spacing: { after: n }, children: [] });
const h = (text, level) => new Paragraph({ text, heading: level });

function block(lines, { mono = false, bg = CODEBG } = {}) {
  const rows = lines.map((line) => new TableRow({
    children: [new TableCell({
      width: { size: PAGE_W, type: WidthType.DXA },
      shading: { type: ShadingType.CLEAR, fill: bg, color: "auto" },
      margins: { top: 25, bottom: 25, left: 170, right: 170 },
      borders: noBorders,
      children: [new Paragraph({
        spacing: { after: 0, line: 280 },
        children: [new TextRun({
          text: line || " ", font: mono ? MONO : HAN, size: mono ? 17 : 19,
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
  const cell = (txt, head, w) => new TableCell({
    width: { size: w, type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: ACCENT, color: "auto" } : undefined,
    margins: { top: 70, bottom: 70, left: 120, right: 120 },
    children: [new Paragraph({
      spacing: { after: 0, line: 300 },
      children: bold(txt).map((seg) => new TextRun({
        text: seg.t, bold: head || seg.b, font: HAN, size: 19,
        color: head ? "FFFFFF" : "1A1A1A",
      })),
    })],
  });
  const body = [new TableRow({
    tableHeader: true,
    children: header.map((txt, i) => cell(txt, true, widths[i])),
  })];
  rows.forEach((r) => body.push(new TableRow({
    children: r.map((txt, i) => cell(txt, false, widths[i])),
  })));
  const line = { style: BorderStyle.SINGLE, size: 3, color: RULE };
  return new Table({
    columnWidths: widths, width: { size: PAGE_W, type: WidthType.DXA },
    borders: { top: line, bottom: line, left: line, right: line,
               insideHorizontal: line, insideVertical: line },
    rows: body,
  });
}

function callout(title, lines, bg = WARNBG) {
  const rows = [[title]].concat(lines.map((l) => [l]));
  return new Table({
    columnWidths: [PAGE_W], width: { size: PAGE_W, type: WidthType.DXA },
    borders: noBorders,
    rows: rows.map((r, i) => new TableRow({
      children: [new TableCell({
        width: { size: PAGE_W, type: WidthType.DXA },
        shading: { type: ShadingType.CLEAR, fill: bg, color: "auto" },
        margins: { top: 50, bottom: 50, left: 170, right: 170 },
        borders: noBorders,
        children: [new Paragraph({
          spacing: { after: 0, line: 300 },
          children: bold(r[0]).map((s) => new TextRun({
            text: s.t, bold: s.b || i === 0, font: HAN, size: 19,
          })),
        })],
      })],
    })),
  });
}

// Figures from the maintainer's edit, kept rather than redrawn.
// A tall figure placed after text gets clipped rather than flowed, so one that needs
// most of a page is given its own. Measured the hard way: the 1024x1536 flowchart
// rendered as a 15pt sliver showing only its bottom bar.
function figure(file, caption, widthPt, ownPage) {
  const full = path.join(IMGDIR, file);
  if (!fs.existsSync(full)) {
    return [p("［图：" + caption + " — 生成时未找到 " + file + "］",
              { size: 18, color: "A04040" })];
  }
  const buf = fs.readFileSync(full);
  // keep the aspect ratio of the source
  const dim = pngSize(buf);
  const w = widthPt || 430;
  const hh = Math.round(w * (dim.h / dim.w));
  const out = [];
  if (ownPage) {
    out.push(new Paragraph({ children: [new PageBreak()] }));
  }
  out.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      // lineRule AUTO is load-bearing. The document default sets a fixed line height,
      // which clamps an inline image to one line: the 1024x1536 flowchart rendered as a
      // 15pt sliver showing only its bottom bar, while the extents inside the .docx were
      // correct all along -- so checking the XML said nothing and only looking did.
      spacing: { before: 120, after: 60, line: 240, lineRule: LineRuleType.AUTO },
      children: [new ImageRun({ data: buf, type: "png",
                                transformation: { width: w, height: hh } })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 160 },
      children: [new TextRun({ text: caption, font: HAN, size: 17, color: "5C5C5C" })],
    }));
  return out;
}

function pngSize(buf) {
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
}

// ------------------------------------------------------------------ content

const C = [];

C.push(new Paragraph({ text: "RBPfinder 使用指南", heading: HeadingLevel.TITLE }));
C.push(p("Biomni / Linux 版 · 面向不写代码的使用者 · 对应 v1.2.10+linux",
         { size: 19, color: "5C5C5C", after: 40 }));
C.push(p("从噬菌体基因组里，找出值得优先做实验的受体结合蛋白（RBP）候选。",
         { size: 19, color: "5C5C5C", after: 60 }));
C.push(p("它帮你**缩小实验范围**，不替代实验确认。适用于有尾双链 DNA 噬菌体。",
         { size: 19, color: "5C5C5C", after: 260 }));

// ---------------------------------------------------------------- 01
C.push(h("01｜先跑起来", HeadingLevel.HEADING_1));
C.push(p("三步，不需要先学任何命令。"));

C.push(h("第一步：上传基因组文件", HeadingLevel.HEADING_2));
C.push(table(["你手里的文件", "你需要做什么"], [
  ["**DNA FASTA**（.fa / .fasta / .fna）",
   "直接上传，并说明「这是未注释基因组」。Biomni 会先做注释。"],
  ["**GenBank**（.gb / .gbk）",
   "直接上传。Biomni 会检查里面有没有基因注释和蛋白序列；合格就直接用。"],
], [34, 66]));
C.push(note("不要只改文件后缀，也不要把单独的蛋白 FASTA 当基因组上传。"));

C.push(h("第二步：把下面这段话发给 Biomni", HeadingLevel.HEADING_2));
C.push(block([
  "请调用 rbpfinder skill，分析我上传的「我的基因组.fasta」。",
  "这是未注释的噬菌体 DNA 基因组；宿主未知，是否环状也不确定。",
  "先用 fast 路线；若建议改用 accurate，请先说明理由与额外耗时。",
  "先检查环境和文件；如需安装或下载大型资源，请先告诉我。",
  "请给出 Primary、Rescue，并用中文解释结果限制。",
  "不要把序列提交到外部在线分析服务。",
]));
C.push(note("宿主和环状信息如果知道，直接替换掉「未知／不确定」——但不要为了填满而猜。"));

C.push(h("第三步：只先看三个问题", HeadingLevel.HEADING_2));
C.push(table(["你想知道", "看哪里"], [
  ["**优先验证谁？**", "Primary（优先候选）"],
  ["**还有哪些值得补？**", "Rescue（补充候选）"],
  ["**这次有什么没查清？**", "完整性与限制说明"],
], [34, 66]));
C.push(gap());

// ---------------------------------------------------------------- 02
C.push(h("02｜它是怎么工作的", HeadingLevel.HEADING_1));
C.push(p("三方分工：**你**提供文件和选路线，**Biomni** 负责注释和跑程序，"
         + "**RBPfinder** 按固定规则给候选分层 —— 不是聊天模型凭印象决定的。"));
C.push(...figure("image1.png",
                 "图 1｜主流程。候选分层由 RBPfinder 按冻结的规则产生。", 360, true));
C.push(callout("一句话记住", [
  "**候选清单 ≠ 已经实验证实的 RBP。**它是一份按证据强弱排好序的待验证名单。",
], OKBG));
C.push(gap());

// ---------------------------------------------------------------- 03  (the ask)
C.push(h("03｜报告里的字母：G、S、D、T 是什么", HeadingLevel.HEADING_1));
C.push(p("报告会写某个候选有「**G+S+T**」这样的标记。这些字母表示"
         + "**它拿到了哪几类证据** —— 字母不同，证据的"
         + "**种类**就不同。"));
C.push(table(["字母", "它回答什么问题", "证据从哪来"], [
  ["**G**\n基因组位置",
   "它在基因组里的**位置和邻居**对不对？尾部基因通常扎堆出现。",
   "落在尾部模块窗口内、和其他候选挨在一起"],
  ["**S**\n序列同源",
   "**别的噬菌体里有没有长得很像的蛋白**？那个蛋白是干什么的？",
   "BLAST 比对本地序列数据库"],
  ["**D**\n功能域",
   "它身上有没有**已知功能的结构域**？",
   "PHROG 注释、HHpred、InterProScan"],
  ["**T**\n三维结构",
   "它**折叠出来长什么样**？像已知的哪个蛋白？",
   "Foldseek 结构比对"],
  ["**C**\n比较基因组",
   "**近缘噬菌体在这个位置变不变**？受体结合蛋白往往变得快。",
   "和近缘基因组比对（需要提供近缘序列）"],
  ["**M**\n机器学习",
   "专门的 RBP 预测模型怎么说？",
   "**本版未实现**，不会出现在报告里"],
], [16, 44, 40]));

C.push(h("为什么要分类 —— 这是看懂分层的关键", HeadingLevel.HEADING_2));
C.push(callout("搜了三个数据库 ≠ 有三条证据", [
  "如果三次搜的都是序列比对，那是**同一类证据看了三遍**，只算**一个 S**。",
  "这是为了防止同一个事实被当成三票。",
], WARNBG));
C.push(p("所以分层里的 **E3 / E2 / E1 数的是「有几个不同字母」**，不是跑了几次搜索。"));
C.push(block([
  "G + S + T   →  E3    三类不同的证据都指向同一结论",
  "G + S       →  E2    两类",
  "G           →  E1    只有一类，或还没解决",
], { mono: true, bg: OKBG }));

C.push(h("两个分层字母连起来读", HeadingLevel.HEADING_2));
C.push(table(["", "它回答", "取值"], [
  ["**R**（前面那个）", "**它是什么**",
   "R1 强候选 ｜ R2 可能是 ｜ R3 属于尾部装置但没有直接证据 ｜ N 有证据表明不是"],
  ["**E**（后面那个）", "**我们看得多仔细**", "E3 三类以上 ｜ E2 两类 ｜ E1 一类或未解决"],
], [20, 24, 56]));
C.push(p("所以 **R2-E2** 读作「可能是 RBP，有两类独立证据支持」；"
         + "**R3-E1** 读作「属于尾部装置，而且我们只看到一类证据」—— "
         + "后者分层低，**很大程度上是因为还没看够**，不是因为已经排除了它。"));
C.push(note("把这两件事分开，是为了让「证据不足」不会被误读成「已经否定」。"));
C.push(gap());

// ---------------------------------------------------------------- 04
C.push(h("04｜怎么提需求", HeadingLevel.HEADING_1));

C.push(h("先选路线：fast 还是 accurate", HeadingLevel.HEADING_2));
C.push(table(["路线", "它多做了什么", "什么时候用"], [
  ["**fast**（默认）", "用 pharokka 做基因注释，然后跑 RBPfinder。",
   "第一次摸底；已有较好注释；想快点拿到清单。"],
  ["**accurate**", "在上面基础上，额外用 phold 改善注释，再跑 RBPfinder。",
   "很多基因标成 hypothetical protein（功能未知）；或 fast 跑完提示证据有限。"],
], [20, 38, 42]));
C.push(p("**这个选择比听起来重要。**accurate 改善的是**注释质量** —— "
         + "而注释决定了候选能不能拿到 **D** 家族。在 T7 这株标准噬菌体上，"
         + "它就是文献确认的那个 RBP **在不在清单里**的区别。"));
C.push(note("accurate 更耗时，具体多久取决于机器和基因组大小。"
            + "它可能改善结果，但不保证一定找全或一定更准。"));

C.push(h("三种常见任务，直接复制", HeadingLevel.HEADING_2));
C.push(p("**A. 只有原始基因组，想快速摸底**", { after: 60 }));
C.push(block([
  "请用 rbpfinder skill 分析我上传的「我的基因组.fasta」，先用 fast。",
  "宿主：[填写已知名称／未知]；基因组：[已知环状／已知线状／不确定]。",
  "先完成必要注释，再给出优先候选、补充候选和结果限制。",
]));
C.push(p("**B. 已有注释文件，不想重复注释**", { before: 100, after: 60 }));
C.push(block([
  "请分析我上传的「已注释基因组.gbk」。",
  "先检查是否包含基因注释及蛋白翻译序列；合格就复用现有注释。",
  "不要混用其他注释来源的基因编号。",
]));
C.push(p("**C. fast 结果有限，想再做一次增强注释**", { before: 100, after: 60 }));
C.push(block([
  "请在已有 fast 结果基础上，评估改用 accurate 是否合适。",
  "先说明需要补跑什么、预计耗时，再经我确认执行。",
  "完成后比较两次候选清单的变化，并解释变化来自哪些新增信息。",
]));

C.push(h("哪些信息最好一起给", HeadingLevel.HEADING_2));
C.push(table(["信息", "为什么有用", "不知道怎么办"], [
  ["**宿主属名**", "只有配好且覆盖该宿主的数据库才用得上这条信息。",
   "写「未知」，不影响运行"],
  ["**环状还是线状**",
   "**最容易悄悄吃亏的一条。**尾部基因如果跨过基因组起点，提名可能丢失，"
   + "而候选池数量看不出变化。", "写「不确定」，让 Biomni 自己查文件"],
  ["**已有材料**", "以前的运行结果、已知 RBP、结构材料都能用上。", "有就给，并说明来源"],
], [22, 48, 30]));
C.push(gap());

// ---------------------------------------------------------------- 05
C.push(h("05｜你会得到什么", HeadingLevel.HEADING_1));

C.push(h("两份清单 + 一个完整性结论", HeadingLevel.HEADING_2));
C.push(table(["你看到", "大白话", "下一步"], [
  ["**Primary**", "工具给的**优先实验候选**，不是「已确认的 RBP」。", "结合实验可行性安排验证"],
  ["**Rescue**", "为**降低漏掉真候选的风险**保留的补充名单，证据没那么强。",
   "别忽略；预算允许就补测"],
  ["完整性：**complete**", "按本次证据和检查，工具认为查得比较完整。", "仍需实验确认"],
  ["完整性：**partial / limited**", "还有没查清的，或这次能用的证据／检查有限。",
   "读具体原因：是缺工具、缺数据，还是查了仍未解决"],
], [24, 46, 30]));
C.push(callout("完整性不是准确率", [
  "搜了很多次，不等于功能已确认；没搜到，也不等于这个基因不是 RBP。",
  "报告里还有一个**内部候选池**（可能十几二十个基因）—— 那是算法用来防漏的，"
  + "**不是实验清单**。",
], WARNBG));

C.push(h("先打开两个文件就够了", HeadingLevel.HEADING_2));
C.push(table(["文件", "用途"], [
  ["**RUN_REPORT.md**",
   "**先读它。**候选清单、入选理由、完整性与限制。也可以让 Biomni 用中文概括。"],
  ["**RBP_candidates.tsv**", "候选表，可以直接用 Excel 打开。"],
  ["run_result.json", "完整运行记录。要追问证据、核对版本或复现时再让 Biomni 读。"],
  ["*_BATCH.tsv", "后续分析的**待办清单** —— 它存在不代表那些分析已经做了。"],
], [26, 74]));

C.push(h("让 Biomni 替你读报告", HeadingLevel.HEADING_2));
C.push(block([
  "请用非专业用户能看懂的中文解释本次结果：",
  "1. Primary 和 Rescue 分别有哪些，各自凭哪几类证据入选（说明 G/S/D/T 分别是什么）？",
  "2. 本次哪些分析做了、哪些没做？还有哪些漏检风险？",
  "3. 有没有需要人工复核的提示？不要把提示当成确认或否定。",
  "4. 记下本次工具版本、规则版本和注释来源，方便以后比较。",
]));

C.push(h("Primary 是空的，怎么办", HeadingLevel.HEADING_2));
C.push(p("**先别解释成「这个噬菌体没有 RBP」。**先查清楚：序列搜索能不能用、"
         + "结构搜索是不是真的跑了、哪些检查没完成。"));
C.push(p("要区分两种情况 —— **证据不够**（某几类证据根本没跑起来，"
         + "所以没东西能到达进 Primary 的层级）和**确实没解决**（该跑的都跑了，"
         + "仍然没有合格的）。前者补配置就能改善，后者是真实结论。"));
C.push(gap());

// ---------------------------------------------------------------- 06
C.push(h("06｜第一次使用与排查", HeadingLevel.HEADING_1));

C.push(h("技能「已启用」≠ 运行环境已齐全", HeadingLevel.HEADING_2));
C.push(...figure("image2.png", "图 2｜技能界面。Enabled 只表示技能已启用。", 300));
C.push(p("技能文件、工具环境、数据库是**三件不同的东西**。开场先发这段："));
C.push(block([
  "我想使用 rbpfinder。请先检查技能是否启用、工具和数据库是否可用。",
  "如果需要部署，请参考官方仓库：https://github.com/Yarkev/RBPfinder",
  "先说明缺少什么、需要下载多少，以及能否复用现有备份。",
]));
C.push(...figure("image3.png",
                 "图 3｜更新卡片。出现时按提示确认保存 —— 仅复制文件不等于完成注册。", 300));
C.push(...figure("image4.png", "图 4｜环境检查说明。它描述的是当时的环境。", 390));

C.push(h("常见情况", HeadingLevel.HEADING_2));
C.push(table(["遇到", "怎么办"], [
  ["**换了会话或机器**",
   "让 Biomni 重新检查。别默认数据库还在，也别直接认定必须全部重下 —— 有备份先评估复用。"],
  ["**提示缺工具／缺数据库**", "先问：缺什么、会影响哪些结论，再决定补装还是接受受限结果。"],
  ["**报告里某个值是 null 或空**",
   "先让 Biomni 解释那个字段 —— 有些「空」是**预期行为**（比如某条路线算不出那个数值），"
   + "不是出错。"],
  ["**结果看起来像旧版本跑的**", "让 Biomni 核对工具版本，确认装的是最新代码。"],
], [26, 74]));
C.push(gap());

// ---------------------------------------------------------------- 07
C.push(h("07｜不该做的事", HeadingLevel.HEADING_1));
C.push(table(["不要", "为什么"], [
  ["**不要为了让结果好看去改规则**", "为迁就答案而改动的阈值，不再是任何东西的证据。"],
  ["**不要让 AI 自己重新排名**",
   "如果它说「虽然工具给了 R3，但我觉得这个更像 RBP」，它是在用工具的权威包装一个猜测。"
   + "它该做的是：**指出分歧、解释原因、说清没有改变工具的结论。**"],
  ["**不要把内部候选池当实验清单**", "那是防漏用的，不是让你去测的。"],
  ["**不要把 *_BATCH.tsv 当成已完成的分析**", "它是待办，不是结果。"],
], [32, 68]));
C.push(gap());

C.push(note("完整技术文档见仓库：packaging/biomni/README.md（部署）、"
            + "GETTING-STARTED.zh.md（完整版）、WORKFLOW.md（流程与已知弱点）。"));
C.push(note("本文档对应 v1.2.10+linux。工具更新后以仓库内文档为准。"));

// ------------------------------------------------------------------ build

const doc = new Document({
  styles: {
    default: {
      document: { run: { font: HAN, size: 21, color: "1A1A1A" },
                  paragraph: { spacing: { line: 320 } } },
      title: { run: { font: HAN, size: 42, bold: true, color: "1A1A1A" },
               paragraph: { spacing: { before: 0, after: 80 }, keepNext: true } },
      heading1: { run: { font: HAN, size: 30, bold: true, color: ACCENT },
                  paragraph: { spacing: { before: 340, after: 150 }, keepNext: true,
                               outlineLevel: 0 } },
      heading2: { run: { font: HAN, size: 23, bold: true, color: "1A1A1A" },
                  paragraph: { spacing: { before: 230, after: 110 }, keepNext: true,
                               outlineLevel: 1 } },
      heading3: { run: { font: HAN, size: 21, bold: true, color: "1A1A1A" },
                  paragraph: { spacing: { before: 180, after: 80 }, keepNext: true,
                               outlineLevel: 2 } },
    },
  },
  sections: [{
    properties: { page: { margin: { top: 1134, bottom: 1134, left: 1247, right: 1247 } } },
    children: C,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = process.argv[2] || "guide.docx";
  fs.writeFileSync(out, buf);
  console.log("wrote", out);
});
