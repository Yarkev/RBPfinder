#!/usr/bin/env python3
"""Build the Chinese user guide PDF for the RBPfinder Biomni/Linux build.

    python3 packaging/biomni/make_guide_pdf.py

Lives in the repository so the PDF can be regenerated when the tool changes. A PDF handed
over once and never rebuilt is exactly the drift this project spent a round fixing -- a
document describing a capability the code no longer has. GETTING-STARTED.zh.md stays the
source of truth; this is the same material laid out for someone reading it cold.

Needs `reportlab` and a CJK font. It uses WenQuanYi Zen Hei, which is in
fonts-wqy-zenhei on Debian/Ubuntu; ReportLab's built-in CID fonts do not render this
text. Update the version strings in the header and footer when the tool version moves.
"""
import pathlib

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

TTC = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
pdfmetrics.registerFont(TTFont("Han", TTC, subfontIndex=0))
pdfmetrics.registerFont(TTFont("HanMono", TTC, subfontIndex=2))

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#5c5c5c")
RULE = colors.HexColor("#d8d8d8")
ACCENT = colors.HexColor("#1d5c8a")
CODEBG = colors.HexColor("#f4f5f7")
WARNBG = colors.HexColor("#fdf4e3")
WARNEDGE = colors.HexColor("#d79a2b")
OKBG = colors.HexColor("#eef5ee")

S = {
    "h1": ParagraphStyle("h1", fontName="Han", fontSize=19, leading=25, textColor=INK,
                         spaceBefore=0, spaceAfter=3),
    "sub": ParagraphStyle("sub", fontName="Han", fontSize=9.5, leading=15, textColor=MUTED,
                          spaceAfter=14),
    "h2": ParagraphStyle("h2", fontName="Han", fontSize=14, leading=19, textColor=ACCENT,
                         spaceBefore=16, spaceAfter=7),
    "h3": ParagraphStyle("h3", fontName="Han", fontSize=11, leading=16, textColor=INK,
                         spaceBefore=11, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="Han", fontSize=9.6, leading=16, textColor=INK,
                        alignment=TA_LEFT, spaceAfter=7),
    "note": ParagraphStyle("note", fontName="Han", fontSize=8.9, leading=14.5,
                           textColor=MUTED, spaceAfter=7),
    "code": ParagraphStyle("code", fontName="HanMono", fontSize=8.3, leading=13.2,
                           textColor=INK, spaceAfter=0),
    "cell": ParagraphStyle("cell", fontName="Han", fontSize=8.7, leading=13.5,
                           textColor=INK),
    "cellmono": ParagraphStyle("cellmono", fontName="HanMono", fontSize=8.1, leading=13,
                               textColor=INK),
    "cellhead": ParagraphStyle("cellhead", fontName="Han", fontSize=8.7, leading=13.5,
                               textColor=colors.white),
}


def P(t, s="p"):
    return Paragraph(t, S[s])


def code(lines, bg=CODEBG, edge=None):
    def esc(l):
        # Paragraph collapses runs of spaces, which destroys any column alignment in a
        # code block. Everything here is pre-formatted, so spaces are content.
        l = l.replace("&", "&amp;").replace("<", "&lt;")
        return l.replace(" ", "&nbsp;") or "&nbsp;"

    body = [[Paragraph(esc(l), S["code"])] for l in lines]
    t = Table(body, colWidths=[165 * mm])
    style = [("BACKGROUND", (0, 0), (-1, -1), bg),
             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
             ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
             ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if edge:
        style.append(("LINEBEFORE", (0, 0), (0, -1), 2.2, edge))
    t.setStyle(TableStyle(style))
    return KeepTogether([Spacer(1, 3), t, Spacer(1, 9)])


def callout(title, lines, bg=WARNBG, edge=WARNEDGE):
    rows = [[Paragraph("<b>%s</b>" % title, S["cell"])]]
    for l in lines:
        rows.append([Paragraph(l, S["cell"])])
    t = Table(rows, colWidths=[165 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, edge),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return KeepTogether([Spacer(1, 3), t, Spacer(1, 10)])


def table(header, rows, widths, mono_cols=()):
    data = [[Paragraph("<b>%s</b>" % h, S["cellhead"]) for h in header]]
    for r in rows:
        data.append([Paragraph(c, S["cellmono"] if i in mono_cols else S["cell"])
                     for i, c in enumerate(r)])
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f8fa")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return KeepTogether([Spacer(1, 2), t, Spacer(1, 10)])


# ---------------------------------------------------------------- content

F = []
F.append(P("RBPfinder 使用指南", "h1"))
F.append(P("Biomni / Linux 版 &nbsp;·&nbsp; v1.2.8+linux &nbsp;·&nbsp; 2026-11-23<br/>"
           "从噬菌体基因组找出受体结合蛋白（RBP）候选", "sub"))

F.append(P("1. 它做什么", "h2"))
F.append(P("给它一个<b>已注释</b>的噬菌体基因组，它输出一份按证据分层的受体结合蛋白候选清单："
           "先测哪几个、哪几个是为保召回留着的、以及这份清单有多完整。", "p"))
F.append(table(
    ["边界", "说明"],
    [["<b>不做注释</b>",
      "注释是 Biomni 的活（pharokka / phold）。这是刻意分工 —— 如果它自己注释，"
      "注释工具升级就能悄悄改变分层结果，而没人会察觉。"],
     ["<b>不联网</b>",
      "所有远程提交路径都被拒绝（退出码 2，在读取任何序列之前）。序列不离开这台机器，"
      "没有开关可以打开。"],
     ["<b>不保证找全</b>",
      "证据不足时它输出 completeness: limited 并说明原因，而不是凑一份好看的清单。"
      "<b>这个状态是结果的一部分，必须读。</b>"],
     ["<b>适用范围</b>",
      "有尾双链 DNA 噬菌体（Caudoviricetes）。RNA / 单链 DNA / 丝状 / 无尾 / 古菌病毒"
      "不在验证范围内。"]],
    [26, 139]))

F.append(P("2. 安装", "h2"))
F.append(P("先判断你是哪种情况 —— 三种的指令完全不同。把对应的那段话直接发给 Biomni。", "p"))

F.append(P("情况 A：全新沙箱", "h3"))
F.append(code([
    "https://github.com/Yarkev/RBPfinder 这是一个 skill，装到这台沙箱上。",
    "克隆后读 packaging/biomni/README.md 按步骤做，特别注意这三节：",
    " - \"Copying the files is NOT registration\"",
    " - \"When the Foldseek CDN refuses you\"",
    " - \"2a. Verify the deployment\"",
]))
F.append(P("耗时主要在下载：结构库约 6.5 GB、ProstT5 权重约 2.3 GB。", "note"))

F.append(P("情况 B：沙箱已配好，只更新版本", "h3"))
F.append(code([
    "把 RBPfinder 更新到最新版并重新部署 skill。",
    "",
    "cd /workspace/rbpfinder_src && git fetch origin && git checkout main",
    "source /workspace/rbpfinder.env",
    "uv pip install --force-reinstall --no-deps -e /workspace/rbpfinder_src",
    "",
    "然后重新部署 skill 包，过 check_skill，并走平台注册流程生成更新卡片。",
    "最后跑一次任意基因组，报告 run_result.json 的 versions 字段。",
]))

F.append(P("情况 C：机器被重建了（OOM / delete）", "h3"))
F.append(code([
    "这台机器被重建了，恢复 RBPfinder 环境。",
    "/shared 上有结构库和 pharokka 库的 tarball 备份，用恢复脚本从那里恢复 ——",
    "不要去跑 foldseek databases，那个 CDN 对本机 IP 限流。",
    "然后 checkout main、重装、重新部署并注册 skill，最后 rbpfinder doctor。",
]))

F.append(callout("安装时有两个坑，每次都会遇到", [
    "<b>① 复制文件不等于注册。</b>把 SKILL.md 复制到位、check_skill 通过 —— 技能栏里"
    "还是看不到它。注册是独立的平台流程：agent 生成一张预览卡片，"
    "<b>你必须确认保存</b>，之后才会出现在技能栏。",
    "<b>② 更新版本时 --force-reinstall 不能省。</b>可编辑安装下 git checkout 只换文件、"
    "不换安装元数据，所以运行会报出一个过期的版本号。",
]))

F.append(P("3. 验证装好了（别跳过）", "h2"))
F.append(code(["rbpfinder doctor"]))
F.append(P("四项核心应该是 AVAILABLE：rules、schema、Local Stage 2、Local Stage 3。"
           "然后跑一次任意基因组，看这个字段：", "p"))
F.append(code(["python -c \"import json; print(json.load("
               "open('<输出目录>/run_result.json'))['versions'])\""]))
F.append(table(
    ["看到", "意思", "做什么"],
    [["consistency: ok", "装对了", "继续"],
     ["consistency: package_metadata_stale", "代码是新的，安装元数据是旧的",
      "按它 note 里的话重装"],
     ["consistency: not_installed", "根本没装上", "重装"]],
    [52, 58, 55], mono_cols=(0,)))
F.append(P("这一步重要是因为：曾经有一整轮测试跑在旧代码上并被报告为通过 —— 一个非可编辑副本"
           "遮蔽了可编辑路径，git checkout 对实际运行的东西毫无影响，而输出里没有任何迹象。", "note"))

F.append(P("4. 怎么用", "h2"))
F.append(P("不需要特殊指令，用自然语言说你要做什么。最短的形式：", "p"))
F.append(code(["找出 /data/01F2.fasta 里的受体结合蛋白候选。宿主是 Salmonella。"]))
F.append(P("但下面三件事值得每次都说 —— 它们会改变结果：", "p"))
F.append(code([
    "用 RBPfinder 分析 /data/01F2.fasta。",
    "宿主 Salmonella。",
    "走 accurate 路线（跑 phold）。",
    "基因组是环状。",
]))
F.append(table(
    ["说什么", "不说会怎样"],
    [["<b>fast 还是 accurate</b>",
      "skill 会主动问你，说了省一轮。这个差别比听起来大 —— 在 T7 上它就是文献确立的 RBP "
      "在不在清单里的区别。"],
     ["<b>宿主属名</b>",
      "不会出错，但少一层证据。只在配了对应宿主库且覆盖该菌属时才用得上，否则记录并跳过。"],
     ["<b>是否环状</b>",
      "<b>最容易悄悄吃亏的一条。</b>GenBank 没声明 topology 时必须显式说。尾模块跨过"
      "原点时提名会丢，<b>而候选池大小不变</b>（别的通道补上了同一个基因），所以没有任何"
      "打印出来的数字会暴露这个退化。"]],
    [34, 131]))

F.append(P("fast 和 accurate 怎么选", "h3"))
F.append(table(
    ["", "fast（默认）", "accurate"],
    [["注释", "pharokka", "pharokka + phold（全基因组）"],
     ["额外耗时", "—", "8 核上 1–2 小时"],
     ["买到什么", "—", "更好的注释：解决 pharokka 留作 hypothetical 的基因"]],
    [24, 46, 95]))
F.append(P("实测在 T7（文献金标准）上：", "p"))
F.append(code([
    "fast      Primary: CDS_0039, CDS_0040              gp17 不在清单里",
    "accurate  Primary: CDS_0039, CDS_0040, CDS_0052    gp17 在清单里（它就是文献 RBP）",
], bg=OKBG))
F.append(P("<b>注释差的基因组选 accurate。</b>一小时 CPU 对一份错的清单来说很便宜。", "p"))

F.append(P("5. 会输出什么", "h2"))
F.append(P("跑完终端会打印一行摘要，并在输出目录写下一组文件。摘要长这样：", "p"))
F.append(code([
    "OK   primary 4 | rescue 0 | completeness partial | coverage reduced",
    "     internal pool 12 of 12 CDS (recall protection, not the shortlist)",
    "     limitation [capability_unavailable]: completeness audit A2 could not run",
    "     schema: PASS   outputs: runs/01F2/",
], bg=OKBG))

F.append(P("输出目录里的文件", "h3"))
F.append(table(
    ["文件", "是什么", "要看吗"],
    [["RUN_REPORT.md", "人读的报告：候选清单、完整性、证据追溯、审计记录", "<b>先看这个</b>"],
     ["RBP_candidates.tsv", "候选表格，可直接导入 Excel：排名、分层、家族、注释", "<b>要看</b>"],
     ["run_result.json", "机器读的完整结果，所有字段都在这里", "追问细节时看"],
     ["all_CDS_screening.tsv", "全部基因的筛查记录 —— 每个基因为什么在或不在池子里", "排查时看"],
     ["*_BATCH.tsv", "需要人工去外部服务跑的批次文件（HHpred / AlphaFold 等）", "想进一步确证时看"]],
    [44, 86, 35], mono_cols=(0,)))

F.append(P("报告的三层结构 —— 按这个顺序读", "h3"))
F.append(table(
    ["层", "含义", "怎么用"],
    [["<b>Primary</b>", "证据最强，支持直接的受体结合作用", "<b>优先去测这些</b>"],
     ["<b>Rescue</b>", "留着是为了保召回，不是因为证据强", "预算允许时再测"],
     ["<b>Completeness</b>", "complete / partial / limited，连同它给的原因",
      "<b>必须读</b> —— 它说明这份清单有多可信"]],
    [30, 80, 55]))
F.append(callout("内部候选池不是实验清单", [
    "报告里还有一个「internal pool」，可能有十几二十个基因。它是为算法设计的"
    "<b>召回保护装置</b>，不是要去测的清单。把它当清单，前面所有分层工作就白做了。",
], bg=WARNBG))

F.append(P("候选表格长什么样", "h3"))
F.append(code([
    "rank  cds_id         tier   families  set_membership  annotation",
    "1     DEMO_CDS_0004  R2-E2  D,G       primary         tail fiber protein",
    "2     DEMO_CDS_0005  R2-E2  D,G       primary         tailspike protein",
    "3     DEMO_CDS_0006  R2-E2  D,G       primary         tip attachment protein",
    "4     DEMO_CDS_0007  R2-E2  D,G       primary         tail sheath and receptor...",
    "5     DEMO_CDS_0000  R3-E2  D,G       reported_only   tail sheath protein",
]))

F.append(P("分层怎么读：两个轴", "h3"))
F.append(P("分层是两个独立的轴。<b>tier_r 是「它是什么」，tier_e 是「我们看得多仔细」。</b>"
           "两者分开，所以一次证据不足的运行不会被误读成一个有信心的否定结论。", "p"))
F.append(table(
    ["tier_r — 它是什么", "tier_e — 看得多仔细"],
    [["<b>R1</b>　强 RBP：≥2 个受体结合家族，且含一个直接家族",
      "<b>E3</b>　≥3 个独立证据家族"],
     ["<b>R2</b>　可能是 RBP / 召回保护", "<b>E2</b>　2 个"],
     ["<b>R3</b>　属于受体结合装置，但没有直接的识别证据", "<b>E1</b>　1 个，或未解决"],
     ["<b>N</b>　有正面证据表明它不是 RBP", ""]],
    [100, 65]))
F.append(P("所以 <b>R2-E2</b> 读作「可能是 RBP，有两条独立证据」；<b>R3-E1</b> 读作"
           "「属于尾部装置，而且我们只看到一条证据」—— 后者的低分层很大程度上是因为看得不够。",
           "note"))

F.append(P("完整性那一段长什么样", "h3"))
F.append(code([
    "completeness assessment    partial  -- sample-specific unresolved RBP risk",
    "assessment coverage        reduced  -- 可选审计在这台机器上能否执行",
    "sample-specific            complete -- 因本机无法执行全部审计而被封顶为 partial",
    "",
    "Limitations bounding this result:",
    "- [installation] completeness audit A2 could not run: missing genomewide T or M",
    "- [note] comparative analysis had no informative cohort",
]))
F.append(P("注意最后两行的区别：<b>[installation]</b> 是你这台机器缺能力（可以补），"
           "<b>[note]</b> 是这次数据本身不适用（无需处理）。", "note"))

F.append(P("Primary 是空的怎么办", "h3"))
F.append(P("<b>空不等于「这里没有 RBP」。</b>先看哪些证据家族根本没跑起来：", "p"))
F.append(code([
    "stage2_local    配了序列数据库吗？",
    "stage3_local    本地结构搜索跑了吗，搜了多大范围？",
    "limitations     哪些审计报告自己无法执行？",
]))
F.append(P("然后判断是哪一种：<b>证据饥饿</b>（某些家族从来没跑，所以没东西能到达进 Primary 的"
           "层级）还是<b>基因组本身确实没解决</b>（全都跑了还是没有合格的）。"
           "把没跑起来的家族和结果一起报告 —— 不要把一次饥饿的运行当成生物学发现。", "p"))

F.append(P("拿到结果后值得追问的两句", "h3"))
F.append(code([
    "versions 和 vocabulary 字段是什么？",
    "presentation_signals 里有没有触发？",
]))
F.append(P("前者决定这批数字以后还能不能和别的批次比；后者是候选校正提示 ——"
           "部件名假阳性、伴侣基因邻接、证据方向冲突。", "note"))

F.append(P("6. 常见问题", "h2"))
F.append(table(
    ["看到什么", "怎么办"],
    [["probability_pct 全是 null",
      "<b>预期行为。</b>这条路线上序列没有骨架坐标，算不出概率。什么都不用做，别重试。"],
     ["A2 could not run: missing_genomewide_T_or_M",
      "<b>默认路线的设计行为，不是故障。</b>说明完整性上限即可。"],
     ["Local Stage 3 UNAVAILABLE",
      "结构库、ProstT5 权重或 foldseek 缺一个。看 README 第 1 节。"],
     ["stage3_local.cds_searched: 0",
      "用了 phold 的 TSV 作输入 —— 那个表格没有蛋白序列列。改用 --genbank phold.gbk。"],
     ["foldseek 下载报 429",
      "CDN 对数据中心 IP 限流。README 里「When the Foldseek CDN refuses you」有四条替代路径。"],
     ["pharokka install 报 MD5 不符",
      "实际是下到了几百字节的拦截页。<b>先看文件大小</b>，再走手动安装。"],
     ["昨天还在的工具不见了",
      "/opt/conda 和 apt 装的包不过休眠。所有环境放 /workspace。"],
     ["结果像是旧代码跑的",
      "非可编辑副本遮蔽了可编辑路径。见本指南第 3 节。"]],
    [58, 107], mono_cols=(0,)))

F.append(P("7. 不该做的事", "h2"))
F.append(table(
    ["不该做的事", "为什么"],
    [["<b>不要改阈值来让结果好看</b>",
      "一个为了迁就答案而改动的阈值，不再是任何东西的证据。"],
     ["<b>不要让 agent 自己重新排名</b>",
      "如果它说「虽然工具给了 R3，但我认为这个更可能是 RBP」，它在用工具的权威包装一个猜测。"
      "它<b>应该</b>做的是：指出分歧、解释机制、说清它没有改变工具的结论。"
      "<b>指出分歧不是重新排名。</b>"],
     ["<b>不要手工建结构库</b>",
      "能建成，但没有 taxonomy 文件，搜索会在运行时死掉。doctor 把这种库报成 "
      "CONFIGURED, INVALID 是刻意的。"],
     ["<b>不要用 phold 的 TSV 作输入然后期待结构证据</b>",
      "那个表格没有蛋白序列列。用 phold.gbk。"]],
    [52, 113]))

F.append(Spacer(1, 6))
F.append(P("完整参考：仓库 packaging/biomni/README.md（部署细节）、"
           "packaging/biomni/GETTING-STARTED.zh.md（本指南的 markdown 版，随代码更新）、"
           "WORKFLOW.md §5（错答案可能来自哪里，共 16 条已知弱点）。<br/>"
           "本 PDF 对应 v1.2.8+linux；代码更新后以仓库内文档为准。", "note"))

# ---------------------------------------------------------------- build

OUT = pathlib.Path(
    __file__).resolve().parent / "RBPfinder-Biomni-shiyong-zhinan.pdf"


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFont("Han", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(22 * mm, 12 * mm, "RBPfinder v1.2.8+linux · Biomni / Linux 版使用指南")
    canvas.drawRightString(188 * mm, 12 * mm, "第 %d 页" % doc.page)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(22 * mm, 15.5 * mm, 188 * mm, 15.5 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(str(OUT), pagesize=A4,
                      leftMargin=22 * mm, rightMargin=22 * mm,
                      topMargin=18 * mm, bottomMargin=20 * mm,
                      title="RBPfinder Biomni/Linux 版使用指南",
                      author="RBPfinder", subject="安装与使用")
frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(F)
print("wrote", OUT)
