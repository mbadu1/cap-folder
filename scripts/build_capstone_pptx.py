"""Rebuild the capstone talk as a widescreen PowerPoint."""

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

W, H = Inches(13.333), Inches(7.5)

INK = RGBColor(0x16, 0x1C, 0x24)
NAVY = RGBColor(0x1B, 0x3A, 0x4B)
PAPER = RGBColor(0xF7, 0xF4, 0xEF)
CREAM = RGBColor(0xFF, 0xFC, 0xF8)
COPPER = RGBColor(0xC4, 0x5C, 0x26)
SAGE = RGBColor(0x2F, 0x6F, 0x5E)
SLATE = RGBColor(0x5E, 0x67, 0x70)
LINE = RGBColor(0xE3, 0xDB, 0xD1)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CARD = RGBColor(0xFF, 0xFF, 0xFF)

NSMAP = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
         "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
         "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def set_run(run, text, font, size, color, bold=False):
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.italic = False


def add_text(slide, text, x, y, w, h, font="Calibri", size=16, color=INK,
             bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = Emu(0)
    tf.margin_right = Emu(0)
    tf.margin_top = Emu(0)
    tf.margin_bottom = Emu(0)
    try:
        tf._txBody.bodyPr.set("anchor", {MSO_ANCHOR.TOP: "t", MSO_ANCHOR.MIDDLE: "ctr", MSO_ANCHOR.BOTTOM: "b"}[anchor])
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_before = Pt(0)
    p.space_after = Pt(0)
    set_run(p.add_run(), text, font, size, color, bold)
    return box


def rect(slide, x, y, w, h, fill, line=None, rounded=False):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1)
    if rounded:
        sh.adjustments[0] = 0.08
    # no shadow
    spPr = sh._element.spPr
    effect = spPr.find(qn("a:effectLst"))
    if effect is not None:
        spPr.remove(effect)
    return sh


def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def footer(slide, section, n, total=15, dark=False):
    ink = RGBColor(0xC9, 0xD0, 0xD6) if dark else SLATE
    rule = RGBColor(0x2C, 0x3E, 0x4E) if dark else LINE
    rect(slide, 0.55, 7.08, 12.23, 0.01, rule)
    add_text(slide, section, 0.55, 7.14, 8, 0.28, size=11, color=ink)
    add_text(slide, f"{n}  /  {total}", 10.4, 7.14, 2.4, 0.28, size=11, color=ink, align=PP_ALIGN.RIGHT)


def kicker(slide, text):
    add_text(slide, text.upper(), 0.55, 0.28, 10, 0.28, size=12, color=COPPER, bold=True)


def title(slide, text, y=0.52, h=0.7, size=32):
    add_text(slide, text, 0.55, y, 12.2, h, font="Georgia", size=size, color=INK)


def fade(slide):
    sld = slide._element
    node = etree.SubElement(sld, qn("p:transition"))
    node.set("spd", "med")
    etree.SubElement(node, qn("p:fade"))


def click_fades(slide, shapes, start_delay_ms=200):
    """Fade content shapes in, one click at a time."""
    if not shapes:
        return
    sld = slide._element
    # build a main sequence: first shape on click with delay, later shapes on next click
    child_pars = []
    nid = 3
    for i, sh in enumerate(shapes):
        spid = str(sh.shape_id)
        delay = str(start_delay_ms if i == 0 else 0)
        evt = "onClick" if i else None
        cond = f'<p:cond delay="{delay}"/>' if i == 0 else '<p:cond delay="0"/>'
        # nodeType clickEffect for i>0
        node = "clickEffect" if i else "withEffect"
        block = f'''
        <p:par xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
          <p:cTn id="{nid}" fill="hold" nodeType="{node}">
            <p:stCondLst>{cond}</p:stCondLst>
            <p:childTnLst>
              <p:animEffect transition="in" filter="fade">
                <p:cBhvr>
                  <p:cTn id="{nid+1}" dur="400"/>
                  <p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>
                </p:cBhvr>
              </p:animEffect>
            </p:childTnLst>
          </p:cTn>
        </p:par>'''
        child_pars.append(block)
        nid += 2
    xml = f'''
    <p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:tnLst>
        <p:par>
          <p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">
            <p:childTnLst>
              <p:seq concurrent="1" nextAc="seek">
                <p:cTn id="2" dur="indefinite" nodeType="mainSeq">
                  <p:childTnLst>
                    {''.join(child_pars)}
                  </p:childTnLst>
                </p:cTn>
                <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
                <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
              </p:seq>
            </p:childTnLst>
          </p:cTn>
        </p:par>
      </p:tnLst>
    </p:timing>'''
    sld.append(etree.fromstring(xml))


def new(prs, paper=True):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg(slide, PAPER if paper else NAVY)
    fade(slide)
    return slide


def card(slide, x, y, w, h, fill=CARD):
    return rect(slide, x, y, w, h, fill, rounded=True)


def build():
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    prs.core_properties.title = "Is AI killing the Internet?"
    prs.core_properties.author = "Zherui Zhang, Ziyang Qin, Michael Badu"

    # 1 title
    s = new(prs, paper=False)
    rect(s, 0, 0, 0.18, 7.5, COPPER)
    add_text(s, "CAPSTONE  ·  SUBSTACK AND MEDIUM", 0.7, 1.55, 11, 0.35, size=13, color=COPPER, bold=True)
    add_text(s, "Is AI killing\nthe Internet?", 0.7, 2.0, 10, 2.1, font="Georgia", size=54, color=WHITE)
    add_text(s, "An atlas of AI model use on Substack and Medium", 0.72, 4.35, 10, 0.4, size=20, color=RGBColor(0xD5, 0xDD, 0xE4))
    rect(s, 0.72, 5.05, 1.4, 0.035, COPPER)
    add_text(s, "Zherui Zhang    ·    Ziyang Qin    ·    Michael Badu", 0.72, 5.25, 10, 0.35, size=16, color=WHITE)

    # 2 motivation
    s = new(prs)
    kicker(s, "Why we are asking")
    title(s, "AI is already in the writing.")
    items = [
        ("01", "Everyday use", "AI now sits inside ordinary writing and communication, not only in demos."),
        ("02", "A known pattern", "Earlier work finds homogenization inside AI-generated text."),
        ("03", "Families differ", "GPT, Claude, and Gemini can carry different views and different styles."),
    ]
    reveals = []
    for i, (n, head, body) in enumerate(items):
        x = 0.55 + i * 4.15
        card(s, x, 1.7, 3.95, 3.35)
        rect(s, x, 1.7, 3.95, 0.08, COPPER if i < 2 else SAGE)
        add_text(s, n, x + 0.28, 1.95, 2, 0.4, font="Georgia", size=22, color=COPPER)
        add_text(s, head, x + 0.28, 2.5, 3.4, 0.7, font="Georgia", size=22, color=INK)
        add_text(s, body, x + 0.28, 3.35, 3.4, 1.3, size=16, color=SLATE)
        reveals.append(s.shapes[-1])
    bar = rect(s, 0.55, 5.25, 12.23, 1.45, NAVY, rounded=True)
    add_text(s, "OUR GOAL", 0.85, 5.42, 3, 0.3, size=12, color=COPPER, bold=True)
    add_text(s, "Study model use, topics, engagement, and similarity across model families on Substack and Medium.",
             0.85, 5.78, 11.5, 0.7, size=18, color=WHITE)
    footer(s, "Motivation", 2)
    click_fades(s, [bar])

    # 3 questions
    s = new(prs)
    kicker(s, "What we will answer")
    title(s, "Four questions, one corpus.")
    qs = [
        ("1", "Presence", "Which model families appear, and how does that use change over time?"),
        ("2", "Topics and ideas", "Do families show up in different topics, or take different positions?"),
        ("3", "Engagement", "For the same writer, do some families draw more response?"),
        ("4", "Similarity", "Is writing linked to different families becoming more alike?"),
    ]
    for i, (n, head, body) in enumerate(qs):
        y = 1.6 + i * 1.25
        card(s, 0.55, y, 12.23, 1.12)
        circ = rect(s, 0.78, y + 0.26, 0.6, 0.6, NAVY, rounded=True)
        add_text(s, n, 0.78, y + 0.36, 0.6, 0.42, font="Georgia", size=18, color=WHITE, align=PP_ALIGN.CENTER)
        add_text(s, head, 1.65, y + 0.16, 4, 0.35, size=14, color=COPPER, bold=True)
        add_text(s, body, 1.65, y + 0.5, 10.6, 0.48, size=18, color=INK)
    footer(s, "Research questions", 3)

    # 4 design
    s = new(prs)
    kicker(s, "How the data is built")
    title(s, "Creators in. Posts measured. Months analyzed.")
    steps = [
        ("Sample", "Substack author\nor Medium writer", "The unit we draw"),
        ("Measure", "The post\nor article", "The unit we read"),
        ("Analyze", "Creator\nby month", "The unit we model"),
    ]
    for i, (head, mid, foot) in enumerate(steps):
        x = 0.55 + i * 4.15
        card(s, x, 1.65, 3.7, 2.7)
        add_text(s, f"0{i+1}", x + 0.25, 1.82, 2, 0.35, font="Georgia", size=18, color=COPPER)
        add_text(s, head, x + 0.25, 2.2, 3.2, 0.4, font="Georgia", size=22, color=INK)
        add_text(s, mid, x + 0.25, 2.7, 3.2, 0.9, size=16, color=SLATE)
        add_text(s, foot, x + 0.25, 3.7, 3.2, 0.4, size=13, color=COPPER, bold=True)
        if i < 2:
            add_text(s, "→", x + 3.55, 2.6, 0.5, 0.4, font="Georgia", size=22, color=COPPER)
    facts = [
        ("2,500", "creators per platform,\npopular plus topic strata"),
        ("Up to 2", "texts kept per\ncreator-month"),
        ("2020–2026", "January 2020 through\n17 September 2026"),
    ]
    for i, (big, small) in enumerate(facts):
        x = 0.55 + i * 4.15
        add_text(s, big, x, 4.6, 3.8, 0.5, font="Georgia", size=26, color=NAVY)
        add_text(s, small, x, 5.15, 3.8, 0.8, size=14, color=SLATE)
    footer(s, "Corpus and sampling", 4)

    # 5 progress
    s = new(prs)
    kicker(s, "Where the project stands")
    title(s, "Collection is far enough to start the science.")
    done = [
        "Substack and Medium have both been scraped.",
        "The repo holds the collection tools, the shared schema, and the frozen Substack assignment manifests.",
        "Exports are reconciled: unique creators, posts, date coverage, and usable full text, by platform.",
        "Duplicates, missing bodies, and coverage by month and topic are quantified.",
    ]
    for i, line in enumerate(done):
        y = 1.65 + i * 1.15
        card(s, 0.55, y, 12.23, 1.02)
        rect(s, 0.78, y + 0.3, 0.42, 0.42, SAGE, rounded=True)
        add_text(s, "✓", 0.78, y + 0.34, 0.42, 0.36, size=16, color=WHITE, align=PP_ALIGN.CENTER, bold=True)
        add_text(s, line, 1.45, y + 0.28, 10.9, 0.5, size=18, color=INK)
    footer(s, "Current progress", 5)

    # 6 detector
    s = new(prs)
    kicker(s, "Shared step 1")
    title(s, "The posts rarely name the model.")
    add_text(s, "So we estimate the source, and we leave unclear cases unresolved.", 0.55, 1.35, 12, 0.4, size=18, color=SLATE)
    steps = [
        ("1", "Known examples", "Human writing, plus recorded outputs from GPT, Claude, and Gemini."),
        ("2", "A detector", "Train it on the text patterns that separate those sources."),
        ("3", "A score", "For each article, a probability per class. Unclear stays unresolved."),
    ]
    for i, (n, head, body) in enumerate(steps):
        x = 0.55 + i * 4.15
        card(s, x, 1.95, 3.95, 2.15)
        add_text(s, n, x + 0.22, 2.1, 0.5, 0.4, font="Georgia", size=22, color=COPPER)
        add_text(s, head, x + 0.7, 2.16, 3, 0.4, font="Georgia", size=20, color=INK)
        add_text(s, body, x + 0.22, 2.7, 3.5, 1.15, size=15, color=SLATE)
    add_text(s, "ONE POST, AS AN ILLUSTRATION", 0.55, 4.35, 8, 0.3, size=12, color=COPPER, bold=True)
    segs = [("GPT  80%", 0.80, COPPER), ("Claude  10%", 0.10, NAVY), ("Gemini  5%", 0.05, SAGE), ("Human  5%", 0.05, SLATE)]
    x0 = 0.55
    width = 12.23
    for name, frac, col in segs:
        rect(s, x0, 4.75, width * frac - 0.04, 0.38, col)
        x0 += width * frac
    for i, (name, frac, col) in enumerate(segs):
        x = 0.55 + i * 3.05
        rect(s, x, 5.32, 0.22, 0.22, col)
        add_text(s, name, x + 0.32, 5.26, 2.5, 0.32, size=14, color=INK, bold=True)
    add_text(s, "Questions 1–4 all start from these scores.", 0.55, 5.75, 12, 0.35, size=16, color=SLATE)
    footer(s, "Estimating model use", 6)

    # 7 validation
    s = new(prs)
    kicker(s, "Shared step 2")
    title(s, "We use the scores only after we know the misses.")
    checks = [
        ("Held-out texts", "Sources the detector never saw in training."),
        ("Realistic edits", "Proofreading, rewriting, and mixed human–AI text."),
        ("A full error count", "Right family, wrong family, missed model text, and cases left open."),
    ]
    for i, (head, body) in enumerate(checks):
        y = 1.6 + i * 1.15
        card(s, 0.55, y, 8.15, 1.02)
        add_text(s, head, 0.8, y + 0.14, 7.6, 0.35, font="Georgia", size=18, color=INK)
        add_text(s, body, 0.8, y + 0.5, 7.6, 0.38, size=15, color=SLATE)
    card(s, 8.95, 1.6, 3.83, 4.45, NAVY)
    add_text(s, "TARGET", 9.2, 1.9, 3.3, 0.3, size=12, color=COPPER, bold=True)
    add_text(s, "9 in 10", 9.2, 2.4, 3.3, 0.9, font="Georgia", size=40, color=WHITE)
    add_text(s, "Assigned family labels should be correct, for each family, on the test set.", 9.2, 3.45, 3.3, 1.3, size=15, color=RGBColor(0xD5, 0xDD, 0xE4))
    add_text(s, "Also report how many texts the detector is willing to classify.", 9.2, 4.9, 3.3, 0.9, size=14, color=RGBColor(0xD5, 0xDD, 0xE4))
    footer(s, "Checking the detector", 7)

    # 8 Q1
    s = new(prs)
    kicker(s, "Question 1")
    title(s, "Which models are gaining ground?")
    card(s, 0.55, 1.6, 5.9, 4.9)
    add_text(s, "Two averages, in that order", 0.8, 1.8, 5.4, 0.4, font="Georgia", size=20, color=INK)
    add_text(s, "1", 0.85, 2.5, 0.4, 0.4, font="Georgia", size=20, color=COPPER)
    add_text(s, "Inside the creator-month", 1.35, 2.45, 4.7, 0.35, size=16, color=INK, bold=True)
    add_text(s, "Average the model probabilities across the posts we kept for that creator that month.", 1.35, 2.85, 4.7, 0.8, size=14, color=SLATE)
    add_text(s, "2", 0.85, 3.75, 0.4, 0.4, font="Georgia", size=20, color=COPPER)
    add_text(s, "Across creators", 1.35, 3.7, 4.7, 0.35, size=16, color=INK, bold=True)
    add_text(s, "Average those creator-month means. One series per family, separately on each platform.", 1.35, 4.1, 4.7, 0.9, size=14, color=SLATE)
    add_text(s, "Resample creators for the intervals.", 0.85, 5.55, 5.2, 0.5, size=15, color=NAVY, bold=True)
    card(s, 6.7, 1.6, 6.08, 4.9, NAVY)
    add_text(s, "THE ESTIMATOR", 6.95, 1.85, 5.5, 0.3, size=12, color=COPPER, bold=True)
    add_text(s, "s  =  mean over creators\nof  ( mean over that\ncreator’s posts )", 6.95, 2.35, 5.5, 1.7, font="Georgia", size=22, color=WHITE)
    add_text(s, "p  platform\nt  month\nm  model family\nI    the texts we retained", 6.95, 4.3, 5.5, 1.7, size=16, color=RGBColor(0xD5, 0xDD, 0xE4))
    footer(s, "Question 1", 8)

    # 9 Q2
    s = new(prs)
    kicker(s, "Question 2")
    title(s, "Which topics travel with each model?")
    cols = [
        ("1", "Find the topics", "Embed the article bodies. Cluster them with k-means."),
        ("2", "Compare families", "Multinomial logit of topic membership on model probabilities, month, and length. Platforms stay separate."),
        ("3", "Read the ideas", "On a few social issues, code the position the post takes. Same regression, same controls."),
    ]
    for i, (n, head, body) in enumerate(cols):
        x = 0.55 + i * 4.15
        card(s, x, 1.65, 3.95, 3.7)
        add_text(s, n, x + 0.25, 1.85, 1, 0.45, font="Georgia", size=26, color=COPPER)
        add_text(s, head, x + 0.25, 2.45, 3.45, 0.7, font="Georgia", size=22, color=INK)
        add_text(s, body, x + 0.25, 3.3, 3.45, 1.7, size=15, color=SLATE)
    add_text(s, "Output: a topic association and a position association for each family, with uncertainty clustered by creator.",
             0.55, 5.6, 12.2, 0.7, size=16, color=INK)
    footer(s, "Question 2", 9)

    # 10 Q3
    s = new(prs)
    kicker(s, "Question 3")
    title(s, "Does model use predict engagement?")
    add_text(s, "Same writer, same month. What is left is the model score.", 0.55, 1.4, 12, 0.4, size=18, color=SLATE)
    parts = [
        ("Outcome", "log(1 + reactions, comments, or claps). We audit which of these actually exist before we pick."),
        ("Held fixed", "A creator effect and a month effect. Errors clustered by creator."),
        ("The signal", "The detector’s probability for each AI family."),
        ("Controls", "Topic, length, post age at collection, and distribution metadata we can see."),
    ]
    for i, (head, body) in enumerate(parts):
        col, row = i % 2, i // 2
        x, y = 0.55 + col * 6.4, 2.05 + row * 2.15
        card(s, x, y, 6.15, 2.0)
        add_text(s, head, x + 0.28, y + 0.22, 5.6, 0.4, font="Georgia", size=20, color=INK)
        add_text(s, body, x + 0.28, y + 0.75, 5.6, 1.0, size=15, color=SLATE)
    footer(s, "Question 3", 10)

    # 11 Q4
    s = new(prs)
    kicker(s, "Question 4")
    title(s, "Are the families starting to sound alike?")
    rows = [
        ("Match", "Same platform, topic, and month. Similar length. Different writers. Only labels the detector will stand behind."),
        ("Pair", "GPT–Claude, GPT–Gemini, Claude–Gemini. Cosine similarity, plus a separate style measure."),
        ("Track", "Each pair’s average similarity by month. Test for a rise, with topic held in the comparison."),
    ]
    for i, (head, body) in enumerate(rows):
        y = 1.6 + i * 1.45
        card(s, 0.55, y, 12.23, 1.32)
        add_text(s, head, 0.8, y + 0.38, 1.8, 0.5, font="Georgia", size=20, color=COPPER)
        add_text(s, body, 2.8, y + 0.32, 9.6, 0.75, size=17, color=INK)
    add_text(s, "A rising line means writing linked to those two families is becoming more similar.",
             0.55, 6.05, 12.2, 0.45, size=16, color=SLATE)
    footer(s, "Question 4", 11)

    # 12 limits
    s = new(prs)
    kicker(s, "What a result can support")
    title(s, "The claim stays inside this design.")
    limits = [
        ("Two platforms", "Accessible English posts on Substack and Medium. Other sites, and text we cannot see, sit outside the result."),
        ("An imperfect detector", "Human edits and new model versions will confuse it. Uncertain cases stay unresolved."),
        ("Choice, not cause", "Writers pick tools for their own reasons. Higher engagement is an association, with creator and month held fixed."),
        ("Similarity has other sources", "Shared topics and repeated text can look like convergence. We match topics, drop duplicates, and use more than one measure."),
    ]
    for i, (head, body) in enumerate(limits):
        col, row = i % 2, i // 2
        x, y = 0.55 + col * 6.4, 1.6 + row * 2.45
        card(s, x, y, 6.15, 2.28)
        rect(s, x, y, 0.08, 2.28, COPPER)
        add_text(s, head, x + 0.35, y + 0.25, 5.5, 0.45, font="Georgia", size=20, color=INK)
        add_text(s, body, x + 0.35, y + 0.85, 5.5, 1.2, size=15, color=SLATE)
    footer(s, "Limits", 12)

    # 13 plan
    s = new(prs)
    kicker(s, "This semester")
    title(s, "A paper, ready to send.")
    plan = [
        ("1", "Lock the data", "Coverage checks. Decide which models and periods the detector can support."),
        ("2", "Run 1 through 4", "Then ask whether the answers hold under other measurements."),
        ("3", "Write as we go", "Data and methods now. Results and discussion as each analysis lands."),
        ("4", "Revise and submit", "Advisor notes, a venue, and a submission package."),
    ]
    for i, (n, head, body) in enumerate(plan):
        x = 0.55 + i * 3.2
        card(s, x, 1.65, 3.02, 3.55)
        add_text(s, n, x + 0.2, 1.82, 2.5, 0.5, font="Georgia", size=26, color=COPPER)
        add_text(s, head, x + 0.2, 2.45, 2.6, 0.8, font="Georgia", size=18, color=INK)
        add_text(s, body, x + 0.2, 3.35, 2.6, 1.5, size=14, color=SLATE)
    rect(s, 0.55, 5.45, 12.23, 1.25, NAVY, rounded=True)
    add_text(s, "DELIVERABLE", 0.85, 5.62, 4, 0.28, size=12, color=COPPER, bold=True)
    add_text(s, "A full manuscript, the figures, a reproducible analysis, and a submission plan.",
             0.85, 5.95, 11.5, 0.5, size=18, color=WHITE)
    footer(s, "Semester plan", 13)

    # 14 path
    s = new(prs)
    kicker(s, "The through-line")
    title(s, "From a score to a sentence about culture.")
    path = [
        ("Corpus", "Who wrote,\nand when"),
        ("Detector", "Which family\nthe text resembles"),
        ("Four tests", "Time, topic,\nresponse, likeness"),
        ("Paper", "What held up,\nand what did not"),
    ]
    for i, (head, body) in enumerate(path):
        x = 0.55 + i * 3.2
        card(s, x, 2.15, 2.85, 2.6, NAVY if i == 3 else CARD)
        add_text(s, f"0{i+1}", x + 0.22, 2.32, 2.4, 0.35, size=13, color=COPPER, bold=True)
        add_text(s, head, x + 0.22, 2.75, 2.4, 0.5, font="Georgia", size=22, color=WHITE if i == 3 else INK)
        add_text(s, body, x + 0.22, 3.4, 2.4, 1.0, size=15, color=RGBColor(0xD5, 0xDD, 0xE4) if i == 3 else SLATE)
        if i < 3:
            add_text(s, "→", x + 2.7, 3.1, 0.45, 0.4, font="Georgia", size=20, color=COPPER)
    add_text(s, "We estimate use. We check the estimator. Then we ask whether the writing online is getting harder to tell apart.",
             0.55, 5.2, 12.2, 0.9, font="Georgia", size=20, color=INK)
    footer(s, "Through-line", 14)

    # 15 close
    s = new(prs, paper=False)
    rect(s, 0, 0, 0.18, 7.5, COPPER)
    add_text(s, "QUESTIONS", 0.75, 2.15, 11, 0.4, size=14, color=COPPER, bold=True)
    add_text(s, "Is AI killing\nthe Internet?", 0.75, 2.55, 11, 2.0, font="Georgia", size=48, color=WHITE)
    add_text(s, "Zherui Zhang    ·    Ziyang Qin    ·    Michael Badu", 0.75, 5.15, 11, 0.4, size=16, color=RGBColor(0xD5, 0xDD, 0xE4))
    footer(s, "Discussion", 15, dark=True)

    return prs


if __name__ == "__main__":
    prs = build()
    out = "/Users/kofibadu/Desktop/cap folder/Capstone_presentation.pptx"
    prs.save(out)
    print(out, "slides", len(prs.slides))
