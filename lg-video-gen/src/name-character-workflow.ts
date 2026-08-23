export interface CharacterSeed {
  id: string;
  name: string;
  desire: string;
  fear: string;
  position: string;
  nonNegotiable: string;
  contradiction: string;
  voiceSample: string;
  visualReferences?: string[];
}

export interface NameWorkflowInput {
  title: string;
  whatToDraw: string;
  whyNow: string;
  readerQuestion: string;
  authoredVocabulary: string[];
  characters: CharacterSeed[];
  externalAdvice?: string[];
}

export type WorkflowPhase = "seed" | "character" | "conflict" | "name" | "revision";

export interface WorkflowStep {
  number: number;
  id: string;
  phase: WorkflowPhase;
  action: string;
  artifact: string;
  gate: string;
}

export interface NameWorkflow {
  schema: "cloud.itonami.mangaka.name-character-workflow.v1";
  title: string;
  principle: "no-single-correct-order";
  steps: WorkflowStep[];
  characterCouncil: Array<{
    character: string;
    position: string;
    bestMove: string;
    contradiction: string;
    voiceSample: string;
    visualReferences: string[];
  }>;
  loops: Array<{ from: string; to: string; when: string }>;
}

function required(value: string, label: string): string {
  const result = value.trim();
  if (!result) throw new Error(`${label} must not be empty`);
  return result;
}

export function buildNameWorkflow(input: NameWorkflowInput): NameWorkflow {
  required(input.title, "title");
  required(input.whatToDraw, "whatToDraw");
  required(input.whyNow, "whyNow");
  required(input.readerQuestion, "readerQuestion");
  if (input.characters.length < 2) throw new Error("at least two characters are required to test conflict");
  if (new Set(input.characters.map((character) => character.id)).size !== input.characters.length) {
    throw new Error("character ids must be unique");
  }
  for (const character of input.characters) {
    for (const key of ["id", "name", "desire", "fear", "position", "nonNegotiable", "contradiction", "voiceSample"] as const) {
      required(character[key], `character.${character.id || "unknown"}.${key}`);
    }
  }

  const steps: WorkflowStep[] = [
    { number: 1, id: "draw-desire", phase: "seed",
      action: `「いま描きたいもの」を一文に固定する: ${input.whatToDraw}`,
      artifact: "one-sentence drawing desire", gate: "流行や設定説明ではなく、自分が見たい瞬間になっている" },
    { number: 2, id: "present-position", phase: "seed",
      action: `なぜ今の自分が描くのかを書く: ${input.whyNow}`,
      artifact: "author-position note", gate: "現在の経験・関心との接点を一つ説明できる" },
    { number: 3, id: "vocabulary-remix", phase: "seed",
      action: `自作語彙を組み替える: ${input.authoredVocabulary.join(" / ") || "語彙を3つ集める"}`,
      artifact: "world vocabulary remix", gate: "借用語の寄せ集めではなく、作品内で新しい関係を持つ" },
    { number: 4, id: "character-position", phase: "character",
      action: "各キャラの欲求・恐れ・立場・譲れない線・矛盾を別々に書く",
      artifact: "character position cards", gate: "作者の説明なしでも、同じ事件に違う反応をする" },
    { number: 5, id: "voice-before-profile", phase: "character",
      action: "詳細設定や清書より先に、各キャラの台詞と掛け合いを紙上で試す",
      artifact: "voice and interaction scraps", gate: "名前を隠しても誰の台詞か推測できる" },
    { number: 6, id: "visual-assembly", phase: "character",
      action: "複数資料から顔・服・姿勢を組み、単一資料の写しを避ける",
      artifact: "reference montage and silhouette sheet", gate: "正面顔ではなくシルエットでも識別できる" },
    { number: 7, id: "motion-test", phase: "character",
      action: "組み上げたキャラを歩かせ、振り向かせ、失敗させて外見と人格の一致を試す",
      artifact: "three-action character test", gate: "静止プロフィールにない癖が動作から一つ見つかる" },
    { number: 8, id: "character-council", phase: "conflict",
      action: "同じ問題を全員へ渡し、それぞれが自分の立場から最善手を選ぶ",
      artifact: "character council table", gate: "作者都合で一人だけ不自然に愚かになっていない" },
    { number: 9, id: "collision", phase: "conflict",
      action: "両立しない最善手を衝突させ、作者が先に勝者を決めず結果を観察する",
      artifact: "cause-and-effect conflict chain", gate: "結果が設定説明ではなく選択と反作用から生じる" },
    { number: 10, id: "protagonist-breakthrough", phase: "name",
      action: "全員が守った均衡を、主人公固有の選択・代償・行動で破る",
      artifact: "breakthrough beat", gate: "主人公だから勝つのではなく、主人公の選択だから局面が変わる" },
    { number: 11, id: "rough-name", phase: "name",
      action: "台詞、立ち位置、作用反作用を粗いコマへ書き出し、結論まで一度通す",
      artifact: "rough name", gate: "絵を整えなくても誰が何を変えたか読める" },
    { number: 12, id: "reader-humility-loop", phase: "revision",
      action: `初見の読者として問い直す: ${input.readerQuestion}。助言「${input.externalAdvice?.join(" / ") || "第三者から一件もらう"}」を一度は反証せずに聞き、採否の理由を書く`,
      artifact: "reader revision and advice ledger", gate: "面白くない箇所、直す箇所、残す意図を各一つ言える" },
  ];

  return {
    schema: "cloud.itonami.mangaka.name-character-workflow.v1",
    title: input.title,
    principle: "no-single-correct-order",
    steps,
    characterCouncil: input.characters.map((character) => ({
      character: character.name,
      position: character.position,
      bestMove: `${character.nonNegotiable}を破らずに「${character.desire}」を得ようとする`,
      contradiction: character.contradiction,
      voiceSample: character.voiceSample,
      visualReferences: character.visualReferences ?? [],
    })),
    loops: [
      { from: "reader-humility-loop", to: "character-position", when: "反応が作者の都合に見える" },
      { from: "reader-humility-loop", to: "collision", when: "結論が説明で決まり、選択から生まれていない" },
      { from: "motion-test", to: "visual-assembly", when: "外見と動作から同じ人格が読めない" },
    ],
  };
}

export function workflowMarkdown(workflow: NameWorkflow): string {
  const steps = workflow.steps.map((step) =>
    `## Step ${step.number}: ${step.id}\n\n` +
    `- Phase: ${step.phase}\n- Action: ${step.action}\n- Artifact: ${step.artifact}\n- Gate: ${step.gate}\n`,
  ).join("\n");
  const council = workflow.characterCouncil.map((entry) =>
    `| ${entry.character} | ${entry.position} | ${entry.bestMove} | ${entry.contradiction} |`,
  ).join("\n");
  const loops = workflow.loops.map((loop) => `- ${loop.from} → ${loop.to}: ${loop.when}`).join("\n");
  return `# ${workflow.title} — ネーム・キャラ制作シート\n\n` +
    `原則: 手順は唯一の正解ではない。ゲートで詰まったら必要なstepへ戻る。\n\n${steps}\n` +
    `## Character council\n\n| Character | Position | Best move | Contradiction |\n|---|---|---|---|\n${council}\n\n` +
    `## Revision loops\n\n${loops}\n`;
}
