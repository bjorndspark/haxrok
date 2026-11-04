/**
 * Every string the debug UI shows, in English and Japanese. Components read
 * the current table with `useMessages()`; pure code (present.ts, symptoms.ts)
 * takes it as an argument. The model's prose comes back in the chosen
 * language from the backend; the backend's own check text (wiring findings,
 * calculations, ERC) stays English.
 */
import { useLanguage, type Language } from "@/shared/lib/language"

import type { Confidence } from "./present"
import type { ConfirmStep, Evidence, ProgressStage, Severity } from "./types"

type StepKey = "upload" | ProgressStage

const plural = (n: number, one: string, many: string) => `${String(n)} ${n === 1 ? one : many}`

const en = {
  header: {
    tagline: "schematic debugging and pre-fab review for KiCad boards",
    newAnalysis: "New analysis",
    light: "Light",
    dark: "Dark",
    toLight: "Switch to light theme",
    toDark: "Switch to dark theme",
    toggleTheme: "Toggle theme",
    /** The button shows the language it switches to, in that language. */
    otherLanguage: "日本語",
    switchLanguage: "日本語に切り替え",
  },
  input: {
    modeAria: "Mode",
    modes: {
      review: {
        label: "Review before you order",
        detail: "Catch schematic mistakes before you pay for boards.",
      },
      debug: {
        label: "Debug a board on the bench",
        detail: "It's built and misbehaves. Find the cause.",
      },
    },
    heading: { review: "Review a schematic before you order", debug: "Diagnose a board problem" },
    intro: {
      review:
        "Drop your KiCad project folder. You get the design mistakes a reviewer would flag, most severe first, each with the evidence and the schematic fix.",
      debug:
        "Drop your KiCad project folder and describe what you see on the bench. You get the likely causes in your schematic, ranked, and how to check each one with a multimeter.",
    },
    dropTitle: "Drop a KiCad project folder",
    or: "or",
    chooseFolder: "Choose folder",
    /** Around the two file types: "Only .kicad_pro and .kicad_sch are sent…" */
    sentBefore: "Only ",
    sentAnd: " and ",
    sentAfter: " are sent; the PCB stays here.",
    sheets: (n: number) => plural(n, "sheet", "sheets"),
    showFiles: "▸ show files",
    hideFiles: "▾ hide files",
    remove: "remove",
    notesTitle: "What the board is for (optional)",
    benchTitle: "What you see on the bench",
    oneObservation: "One observation per line: “Fails on battery”, not “battery”.",
    clear: "clear",
    covered: (done: number, total: number) => `Covered ${String(done)}/${String(total)}`,
    startLine: "Start a line",
    symptoms: (n: number) => plural(n, "symptom", "symptoms"),
    shortLines: (lines: string[]) =>
      lines.length > 1
        ? `Short lines “${lines.slice(0, 2).join("”, “")}” may not stand on their own.`
        : `Short line “${lines[0] ?? ""}” may not stand on its own.`,
    error: "ERROR",
    backToResults: "Back to results",
    submit: { review: "Review schematic", debug: "Analyze schematic" },
  },
  symptoms: {
    hints: {
      power: "What powers it",
      works: "What works",
      fails: "What doesn't",
      measured: "What you measured",
      hot: "Anything hot",
    },
    placeholder: `One observation per line, each standing on its own:
Powered from a battery through the regulator.
Works on USB 5 V: the microcontroller boots.
Fails on battery: the microcontroller does not boot.
When it fails, the 3.3 V rail measures 2.3 V and the supply draws 400 mA.
When it fails, the regulator gets warm.`,
    reviewPlaceholder: `Optional. What the board is for, one note per line:
Powered from a 1S LiPo (3.0-4.2 V) or USB-C 5 V.
Drives a 12 V, 1 A solenoid.
Used outdoors, so it may see -10 °C.`,
    prompts: [
      { label: "Power source", prefix: "Powered from" },
      { label: "What works", prefix: "Works on" },
      { label: "What fails", prefix: "Fails on" },
      { label: "Measurement", prefix: "When it fails, I measured" },
      { label: "Hot part", prefix: "When it fails, this part gets hot:" },
    ],
  },
  run: {
    errors: {
      noProjectFiles: "No .kicad_pro or .kicad_sch files found. Drop the KiCad project folder.",
      dropFirst: "Drop your KiCad project folder first.",
      describeFirst: "Describe what you see on the bench first.",
      closedEarly: "The connection closed before a result arrived.",
      cancelled: "Cancelled.",
      failed: "The analysis failed.",
      readFolder: "Couldn't read the dropped folder.",
      question: "The question failed.",
    },
    trigger: {
      review: "Review before ordering",
      initial: "Initial analysis",
      measurement: (id: string, value: string, step: string) => `${id}: ${value} (step ${step})`,
    },
  },
  analyzing: {
    badge: "ANALYZING",
    usually: "usually 4–5 min",
    cancel: "Cancel",
    steps: {
      upload: { label: "Parse + ERC", detail: "kicad-cli netlist export and ERC" },
      pin_lookup: { label: "Pin lookup", detail: "Datasheet pin tables for the ICs" },
      reasoning: { label: "Diagnosis", detail: "The model traces the circuit" },
      structuring: { label: "Write-up", detail: "Claims, citations and bench steps" },
      checking: { label: "Checking", detail: "Every citation checked against the input" },
    } satisfies Record<StepKey, { label: string; detail: string }>,
    state: { done: "done", active: "running", pending: "waiting" },
  },
  session: {
    run: (n: number) => `Run #${String(n)}`,
    took: (t: string) => ` · took ${t}`,
    failed: "FAILED",
    didntFinish: "This run didn't finish",
    noResultArrived: "No result arrived.",
    tryAgain: "Try again",
    editInput: "Edit input",
    editObservations: "Edit observations",
    noIssuesTitle: "✓ No schematic issues found",
    noIssuesBody:
      "The review traced each block and found nothing to change. It only reads the schematic: layout, footprints and assembly aren't checked.",
    noHypotheses: "The model returned no hypotheses for this input.",
    openQuestions: "Open questions",
    check: "check:",
    glossary: "Glossary",
    noResult: "No result",
    status: { analyzing: "analyzing", review: "review ready", debug: "diagnosis ready" },
    runs: (n: number) => (n === 1 ? "1 run" : `${String(n)} runs, newest first`),
    heading: { review: "Design review", debug: "Ranked hypotheses" },
    downloadReport: "Download report",
  },
  hypothesis: {
    explains: "Explains",
    claimsBacked: "Claims backed",
    labelledBy: (label: string, reason: string) =>
      `! Labelled “${label}” by the model, but the check failed: ${reason}`,
    power: (p: ConfirmStep["power"]) => `power ${p}`,
    tool: {
      multimeter: "multimeter",
      scope: "scope",
      look: "look",
      other: "other",
    } satisfies Record<ConfirmStep["tool"], string>,
    testsCause: "tests the cause",
    checksBoard: "checks the board matches the schematic",
    hardByHand: "! hard by hand",
    rewireBeforePower: "Rewire before power",
    /** Before, between and after the pin and its net (or other pin). */
    setup: { disconnect: ["", " from ", ""], connect: ["", " to ", ""] } as Record<
      "disconnect" | "connect",
      [string, string, string]
    >,
    ifYouSee: "If you see",
    itMeans: "It means",
    supports: (id: string) => `supports ${id}`,
    rulesOut: (id: string) => `rules out ${id}`,
    yourReading: "Your reading",
    readingPlaceholder: "e.g. 3.28 V at TP11",
    rerun: "Re-run with this reading",
    symptoms: "Symptoms",
    symptomsAside: (yes: number, no: number) => `${String(yes)} explained, ${String(no)} not`,
    doesntExplain: "Doesn't explain:",
    evidence: "Evidence and claims",
    evidenceAside: (backed: number, claims: number) =>
      `${String(backed)} of ${String(claims)} backed by your input`,
    backendWiring: "Backend wiring check:",
    backendCalc: "Backend calculation:",
    checkTitle: { review: "Check before you order", debug: "Check this with a multimeter" },
    steps: (n: number) => plural(n, "step", "steps"),
    enterOnLatest: "Enter readings on the latest run.",
    fixTitle: {
      review: "Fix in the schematic before ordering",
      debug: "Fix on the bench and in the next revision",
    },
    reworkOnBoard: "Rework on this board",
    hardByHandNote: (note: string) => `! Hard by hand: ${note}`,
    schematicChange: { review: "Schematic change", debug: "Next schematic revision" },
    unverified: {
      review: "Unverified: settle the issue with the checks above before changing the design.",
      debug: "Rework is unverified: confirm the cause with the steps above first.",
    },
  },
  summary: {
    title: "Design summary",
    counts: (sheets: number, parts: number, nets: number, ics: number) =>
      `${String(sheets)} sheets · ${String(parts)} parts · ${String(nets)} nets · ${String(ics)} ICs · `,
    erc: (n: number) => `${String(n)} ERC`,
    icsAndPinData: "ICs and pin data",
    noFootprint: "no footprint",
    refDesign: "ref design",
    pinTable: "pin table",
    noPinData: "no pin data",
    noPinDataNote:
      "ICs without pin data are still analyzed; facts about their pins come from the model's memory and are marked unverified.",
    wired: (n: number) => `! Wired against the datasheet (${String(n)})`,
    pinOrder: (n: number) =>
      `! Pin order to check against the package's usual pinout (${String(n)})`,
    calculated: (n: number) => `Calculated from your netlist (${String(n)})`,
    /** Before the part names: "Reference designs (3 notes for TPS63020)". */
    references: (n: number) => `Reference designs (${String(n)} notes for `,
    referencesEnd: ")",
    ercFindings: (n: number) => `KiCad ERC findings (${String(n)})`,
    hide: "▾ hide",
    inspect: "▸ inspect",
    notes: "Notes on the board",
    symptoms: "Symptoms",
    andMeasurements: " and measurements",
    step: (id: string) => ` (step ${id})`,
  },
  simulations: {
    title: (n: number) => `Simulations (${String(n)})`,
    netlist: "ngspice netlist",
    suggestions: ["What does this show?", "Is there enough margin?", "What should I check?"],
    reading: "reading the waveform…",
    ask: "Ask",
    askPlaceholder: "Ask about this simulation",
  },
  resultChat: {
    title: "Ask about this result",
    suggestions: {
      review: [
        "Which issue should I fix first?",
        "Is anything here a false alarm?",
        "What did the review not check?",
      ],
      debug: [
        "Which test should I do first?",
        "Why is #1 more likely than #2?",
        "What else could cause this?",
      ],
    },
    reading: "reading the result…",
    ask: "Ask",
    askPlaceholder: "Ask a follow-up about the findings",
  },
  diagram: {
    aria: (ic: string) => `${ic} pins and the nets they are on`,
    inductor: "(inductor)",
    more: (n: number) => `+${String(n)} more`,
  },
  present: {
    /** Labels say what a claim rests on, never that it was verified. */
    evidence: {
      schematic: "Based on your schematic",
      erc: "Based on KiCad's checker",
      datasheet: "Based on the datasheet pin table",
      reference: "Based on the manufacturer's design",
      measurement: "Based on your measurement",
      none: "No direct evidence",
    } satisfies Record<Evidence, string>,
    confidence: {
      verified: "Verified design fault",
      fits: "Fits the symptoms",
      speculative: "Speculative",
    } satisfies Record<Confidence, string>,
    reasoned: "Reasoned: check it",
    severity: {
      will_fail: "Won't work as drawn",
      may_fail: "May fail",
      improve: "Improvement",
    } satisfies Record<Severity, string>,
    miswired: "miswired",
    missing: "missing",
    reason: {
      wiring: (pins: string) =>
        `The backend's wiring check found ${pins} wired against the datasheet, and this hypothesis cites it.`,
      calculation: (ics: string) =>
        `The backend's calculation from your netlist flagged ${ics}, and this hypothesis cites it.`,
      review:
        "The pins it cites are in your schematic, but that they are wrong is the model's reasoning. Settle it with the checks below.",
      fits: "Explains at least half the symptoms, with claims backed by your input.",
      unbacked: "None of its claims is backed by your input.",
      fewer: "Explains fewer than half the symptoms.",
    },
  },
  report: {
    title: (app: string, review: boolean, project: string) =>
      `# ${app} ${review ? "design review" : "report"}: ${project}`,
    run: (n: number, trigger: string, when: string) => `Run #${String(n)} (${trigger}), ${when}`,
    counts: (parts: number, nets: number, erc: number) =>
      `${String(parts)} parts, ${String(nets)} nets, ${String(erc)} ERC findings.`,
    notes: "## Notes on the board",
    symptoms: "## Symptoms",
    noIssues: "No schematic issues found.",
    measurements: "## Measurements",
    issue: "Issue",
    reviewLine: (severity: string, confidence: string, backed: number, claims: number) =>
      `**${severity}** · ${confidence} · ${String(backed)} of ${String(claims)} claims backed`,
    debugLine: (
      confidence: string,
      explained: number,
      symptoms: number,
      backed: number,
      claims: number,
    ) =>
      `**${confidence}** · explains ${String(explained)} of ${String(symptoms)} symptoms · ${String(backed)} of ${String(claims)} claims backed`,
    explains: "Explains:",
    doesntExplain: "Doesn't explain:",
    evidence: "### Evidence",
    labelRemoved: (reason: string) => ` (label removed: ${reason})`,
    check: "### Check before ordering",
    confirm: "### Confirm",
    fix: "### Fix",
    step: (id: string, tool: string, power: string, instruction: string) =>
      `- **${id}** (${tool}, ${power}): ${instruction}`,
    rework: "Rework:",
    nextRevision: "Next revision:",
    simulations: "## Simulations",
    followUps: "## Follow-up questions",
    openQuestions: "## Open questions",
    suggestedCheck: (check: string) => ` (check: ${check})`,
    glossary: "## Glossary",
  },
}

export type Messages = typeof en

const ja: Messages = {
  header: {
    tagline: "KiCad 基板の回路図デバッグと発注前レビュー",
    newAnalysis: "新しい解析",
    light: "ライト",
    dark: "ダーク",
    toLight: "ライトテーマに切り替え",
    toDark: "ダークテーマに切り替え",
    toggleTheme: "テーマ切り替え",
    otherLanguage: "English",
    switchLanguage: "Switch to English",
  },
  input: {
    modeAria: "モード",
    modes: {
      review: {
        label: "発注前にレビュー",
        detail: "基板にお金を払う前に回路図のミスを見つけます。",
      },
      debug: {
        label: "手元の基板をデバッグ",
        detail: "組み立てたが正しく動かない。原因を探します。",
      },
    },
    heading: { review: "発注前に回路図をレビュー", debug: "基板の不具合を診断" },
    intro: {
      review:
        "KiCad のプロジェクトフォルダをドロップしてください。レビュアーが指摘するような設計ミスを、重大なものから順に、根拠と回路図の修正方法つきで示します。",
      debug:
        "KiCad のプロジェクトフォルダをドロップし、ベンチで見えている症状を書いてください。回路図上の考えられる原因を順位づけして示し、それぞれをテスターで確かめる方法を説明します。",
    },
    dropTitle: "KiCad プロジェクトフォルダをドロップ",
    or: "または",
    chooseFolder: "フォルダを選択",
    sentBefore: "送信するのは ",
    sentAnd: " と ",
    sentAfter: " だけです。PCB はこの端末に残ります。",
    sheets: (n: number) => `${String(n)} シート`,
    showFiles: "▸ ファイルを表示",
    hideFiles: "▾ ファイルを隠す",
    remove: "削除",
    notesTitle: "基板の用途 (任意)",
    benchTitle: "ベンチで見えている症状",
    oneObservation: "1 行に 1 つの観察を。「電池」ではなく「電池だと動かない」のように。",
    clear: "クリア",
    covered: (done: number, total: number) => `記入済み ${String(done)}/${String(total)}`,
    startLine: "行を追加",
    symptoms: (n: number) => `症状 ${String(n)} 件`,
    shortLines: (lines: string[]) =>
      `短い行「${lines.slice(0, 2).join("」「")}」は単独では意味が通じないかもしれません。`,
    error: "エラー",
    backToResults: "結果に戻る",
    submit: { review: "回路図をレビュー", debug: "回路図を解析" },
  },
  symptoms: {
    hints: {
      power: "電源",
      works: "動くこと",
      fails: "動かないこと",
      measured: "測定値",
      hot: "発熱",
    },
    placeholder: `1 行に 1 つ、それだけで意味が通じるように:
電池からレギュレータ経由で給電している。
USB 5 V では動く: マイコンが起動する。
電池では動かない: マイコンが起動しない。
動かないとき、3.3 V レールは 2.3 V で、電源は 400 mA 流れている。
動かないとき、レギュレータが温かくなる。`,
    reviewPlaceholder: `任意。基板の用途を 1 行に 1 つ:
1S LiPo (3.0-4.2 V) または USB-C 5 V で給電。
12 V、1 A のソレノイドを駆動する。
屋外で使うので -10 °C になることがある。`,
    prompts: [
      { label: "電源", prefix: "給電は" },
      { label: "動くこと", prefix: "動く条件:" },
      { label: "動かないこと", prefix: "動かない条件:" },
      { label: "測定値", prefix: "動かないときの測定値:" },
      { label: "発熱する部品", prefix: "動かないときに熱くなる部品:" },
    ],
  },
  run: {
    errors: {
      noProjectFiles:
        ".kicad_pro と .kicad_sch のファイルが見つかりません。KiCad のプロジェクトフォルダをドロップしてください。",
      dropFirst: "先に KiCad のプロジェクトフォルダをドロップしてください。",
      describeFirst: "先にベンチで見えている症状を書いてください。",
      closedEarly: "結果が届く前に接続が切れました。",
      cancelled: "キャンセルしました。",
      failed: "解析に失敗しました。",
      readFolder: "ドロップしたフォルダを読み込めませんでした。",
      question: "質問に失敗しました。",
    },
    trigger: {
      review: "発注前レビュー",
      initial: "初回解析",
      measurement: (id: string, value: string, step: string) =>
        `${id}: ${value} (ステップ ${step})`,
    },
  },
  analyzing: {
    badge: "解析中",
    usually: "通常 4〜5 分",
    cancel: "キャンセル",
    steps: {
      upload: { label: "解析 + ERC", detail: "kicad-cli でネットリスト出力と ERC" },
      pin_lookup: { label: "ピン照合", detail: "IC のデータシートのピン表" },
      reasoning: { label: "診断", detail: "モデルが回路をたどる" },
      structuring: { label: "まとめ", detail: "主張、引用、ベンチでの確認手順" },
      checking: { label: "検証", detail: "すべての引用を入力と照合" },
    },
    state: { done: "完了", active: "実行中", pending: "待機" },
  },
  session: {
    run: (n: number) => `実行 #${String(n)}`,
    took: (t: string) => ` · 所要 ${t}`,
    failed: "失敗",
    didntFinish: "この実行は完了しませんでした",
    noResultArrived: "結果が届きませんでした。",
    tryAgain: "再試行",
    editInput: "入力を編集",
    editObservations: "観察を編集",
    noIssuesTitle: "✓ 回路図に問題は見つかりませんでした",
    noIssuesBody:
      "各ブロックをたどりましたが、変更すべき点はありませんでした。確認したのは回路図だけで、レイアウト、フットプリント、実装は確認していません。",
    noHypotheses: "この入力に対して、モデルは仮説を返しませんでした。",
    openQuestions: "未解決の疑問",
    check: "確認:",
    glossary: "用語集",
    noResult: "結果なし",
    status: { analyzing: "解析中", review: "レビュー完了", debug: "診断完了" },
    runs: (n: number) => (n === 1 ? "1 回実行" : `${String(n)} 回実行、新しい順`),
    heading: { review: "設計レビュー", debug: "原因の候補 (順位順)" },
    downloadReport: "レポートをダウンロード",
  },
  hypothesis: {
    explains: "説明できる症状",
    claimsBacked: "裏づけのある主張",
    labelledBy: (label: string, reason: string) =>
      `! モデルは「${label}」としましたが、検証に失敗しました: ${reason}`,
    power: (p: ConfirmStep["power"]) => (p === "on" ? "電源 ON" : "電源 OFF"),
    tool: {
      multimeter: "テスター",
      scope: "オシロスコープ",
      look: "目視",
      other: "その他",
    },
    testsCause: "原因を確かめる",
    checksBoard: "基板が回路図どおりか確かめる",
    hardByHand: "! 手作業では困難",
    rewireBeforePower: "通電前の配線変更",
    setup: { disconnect: ["", " を ", " から外す"], connect: ["", " を ", " につなぐ"] },
    ifYouSee: "結果",
    itMeans: "意味",
    supports: (id: string) => `${id} を支持`,
    rulesOut: (id: string) => `${id} を否定`,
    yourReading: "測定値",
    readingPlaceholder: "例: TP11 で 3.28 V",
    rerun: "この測定値で再実行",
    symptoms: "症状",
    symptomsAside: (yes: number, no: number) =>
      `${String(yes)} 件を説明、${String(no)} 件は説明できない`,
    doesntExplain: "説明できない:",
    evidence: "根拠と主張",
    evidenceAside: (backed: number, claims: number) =>
      `${String(claims)} 件中 ${String(backed)} 件が入力で裏づけあり`,
    backendWiring: "バックエンドの配線チェック:",
    backendCalc: "バックエンドの計算:",
    checkTitle: { review: "発注前の確認", debug: "テスターで確認" },
    steps: (n: number) => `${String(n)} ステップ`,
    enterOnLatest: "測定値は最新の実行に入力してください。",
    fixTitle: {
      review: "発注前に回路図を修正",
      debug: "手元の基板と次の版での修正",
    },
    reworkOnBoard: "この基板の改造",
    hardByHandNote: (note: string) => `! 手作業では困難: ${note}`,
    schematicChange: { review: "回路図の変更", debug: "次の版の回路図" },
    unverified: {
      review: "未検証: 設計を変える前に、上の確認で問題を確かめてください。",
      debug: "改造は未検証です: まず上の手順で原因を確かめてください。",
    },
  },
  summary: {
    title: "設計の概要",
    counts: (sheets: number, parts: number, nets: number, ics: number) =>
      `${String(sheets)} シート · 部品 ${String(parts)} · ネット ${String(nets)} · IC ${String(ics)} · `,
    erc: (n: number) => `ERC ${String(n)}`,
    icsAndPinData: "IC とピンデータ",
    noFootprint: "フットプリントなし",
    refDesign: "リファレンス設計",
    pinTable: "ピン表あり",
    noPinData: "ピンデータなし",
    noPinDataNote:
      "ピンデータのない IC も解析しますが、そのピンに関する事実はモデルの記憶によるもので、未検証として示します。",
    wired: (n: number) => `! データシートと異なる配線 (${String(n)})`,
    pinOrder: (n: number) => `! パッケージの一般的なピン配置と照合すべきピン順 (${String(n)})`,
    calculated: (n: number) => `ネットリストからの計算 (${String(n)})`,
    references: (n: number) => `リファレンス設計 (メモ ${String(n)} 件: `,
    referencesEnd: ")",
    ercFindings: (n: number) => `KiCad ERC の指摘 (${String(n)})`,
    hide: "▾ 隠す",
    inspect: "▸ 詳細",
    notes: "基板についてのメモ",
    symptoms: "症状",
    andMeasurements: "と測定値",
    step: (id: string) => ` (ステップ ${id})`,
  },
  simulations: {
    title: (n: number) => `シミュレーション (${String(n)})`,
    netlist: "ngspice ネットリスト",
    suggestions: ["これは何を示している?", "マージンは十分?", "何を確認すべき?"],
    reading: "波形を読んでいます…",
    ask: "質問",
    askPlaceholder: "このシミュレーションについて質問",
  },
  resultChat: {
    title: "この結果について質問",
    suggestions: {
      review: ["どの問題から直すべき?", "誤検出はある?", "レビューで確認していないことは?"],
      debug: [
        "どのテストから始めるべき?",
        "なぜ #1 が #2 より可能性が高い?",
        "他に考えられる原因は?",
      ],
    },
    reading: "結果を読んでいます…",
    ask: "質問",
    askPlaceholder: "結果について追加で質問",
  },
  diagram: {
    aria: (ic: string) => `${ic} のピンと接続先のネット`,
    inductor: "(インダクタ)",
    more: (n: number) => `他 ${String(n)}`,
  },
  present: {
    evidence: {
      schematic: "回路図に基づく",
      erc: "KiCad のチェッカーに基づく",
      datasheet: "データシートのピン表に基づく",
      reference: "メーカーの設計に基づく",
      measurement: "あなたの測定値に基づく",
      none: "直接の根拠なし",
    },
    confidence: {
      verified: "検証済みの設計不良",
      fits: "症状と合致",
      speculative: "推測",
    },
    reasoned: "推論: 要確認",
    severity: {
      will_fail: "このままでは動かない",
      may_fail: "動かない可能性あり",
      improve: "改善",
    },
    miswired: "配線ミス",
    missing: "なし",
    reason: {
      wiring: (pins: string) =>
        `バックエンドの配線チェックで ${pins} がデータシートと異なる配線と判明し、この仮説はそれを引用しています。`,
      calculation: (ics: string) =>
        `ネットリストからのバックエンドの計算で ${ics} が指摘され、この仮説はそれを引用しています。`,
      review:
        "引用しているピンは回路図にありますが、それが誤りだというのはモデルの推論です。下の確認で確かめてください。",
      fits: "症状の半分以上を説明でき、入力で裏づけのある主張があります。",
      unbacked: "入力で裏づけのある主張がありません。",
      fewer: "説明できる症状が半分未満です。",
    },
  },
  report: {
    title: (app: string, review: boolean, project: string) =>
      `# ${app} ${review ? "設計レビュー" : "レポート"}: ${project}`,
    run: (n: number, trigger: string, when: string) => `実行 #${String(n)} (${trigger})、${when}`,
    counts: (parts: number, nets: number, erc: number) =>
      `部品 ${String(parts)}、ネット ${String(nets)}、ERC の指摘 ${String(erc)} 件。`,
    notes: "## 基板についてのメモ",
    symptoms: "## 症状",
    noIssues: "回路図に問題は見つかりませんでした。",
    measurements: "## 測定値",
    issue: "問題",
    reviewLine: (severity: string, confidence: string, backed: number, claims: number) =>
      `**${severity}** · ${confidence} · 主張 ${String(claims)} 件中 ${String(backed)} 件に裏づけ`,
    debugLine: (
      confidence: string,
      explained: number,
      symptoms: number,
      backed: number,
      claims: number,
    ) =>
      `**${confidence}** · 症状 ${String(symptoms)} 件中 ${String(explained)} 件を説明 · 主張 ${String(claims)} 件中 ${String(backed)} 件に裏づけ`,
    explains: "説明できる:",
    doesntExplain: "説明できない:",
    evidence: "### 根拠",
    labelRemoved: (reason: string) => ` (ラベル削除: ${reason})`,
    check: "### 発注前の確認",
    confirm: "### 確認",
    fix: "### 修正",
    step: (id: string, tool: string, power: string, instruction: string) =>
      `- **${id}** (${tool}、${power}): ${instruction}`,
    rework: "改造:",
    nextRevision: "次の版:",
    simulations: "## シミュレーション",
    followUps: "## 追加の質問",
    openQuestions: "## 未解決の疑問",
    suggestedCheck: (check: string) => ` (確認: ${check})`,
    glossary: "## 用語集",
  },
}

export const MESSAGES: Record<Language, Messages> = { en, ja }

/** The message table for the current UI language. */
export function useMessages(): Messages {
  return MESSAGES[useLanguage((s) => s.language)]
}

/** The current table outside React (event handlers, async actions). */
export function currentMessages(): Messages {
  return MESSAGES[useLanguage.getState().language]
}
