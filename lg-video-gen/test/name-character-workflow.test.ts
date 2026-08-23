import assert from "node:assert/strict";
import { test } from "node:test";
import { buildNameWorkflow, workflowMarkdown, type NameWorkflowInput } from "../src/name-character-workflow.js";

const input: NameWorkflowInput = {
  title: "Trust boundary",
  whatToDraw: "A hand stops before the eyes turn",
  whyNow: "Trust should be shown as a choice, not explained as technology",
  readerQuestion: "Whose next choice does the reader want to see?",
  authoredVocabulary: ["Nei", "hidden process"],
  externalAdvice: ["show the reaction before the explanation"],
  characters: [
    { id: "ren", name: "Ren", desire: "proof", fear: "being wrong", position: "verify first",
      nonNegotiable: "do not accuse without evidence", contradiction: "waiting increases harm",
      voiceSample: "Do not cut it yet.", visualReferences: ["suspended hand"] },
    { id: "nei", name: "Nei", desire: "safety", fear: "losing Ren", position: "disconnect now",
      nonNegotiable: "do not target Ren", contradiction: "silence looks like betrayal",
      voiceSample: "Doubt me, but move." },
  ],
};

test("workflow turns character positions into a 12-step conflict loop", () => {
  const workflow = buildNameWorkflow(input);
  assert.equal(workflow.steps.length, 12);
  assert.equal(workflow.principle, "no-single-correct-order");
  assert.deepEqual(workflow.characterCouncil.map((entry) => entry.character), ["Ren", "Nei"]);
  assert.match(workflow.steps[8].gate, /選択と反作用/);
  assert.match(workflow.steps[11].action, /show the reaction/);
  assert.ok(workflow.loops.some((loop) => loop.to === "character-position"));
});

test("workflow fails closed without a conflict pair or unique ids", () => {
  assert.throws(() => buildNameWorkflow({ ...input, characters: input.characters.slice(0, 1) }));
  assert.throws(() => buildNameWorkflow({ ...input, characters: [input.characters[0], { ...input.characters[1], id: "ren" }] }));
});

test("markdown is a writable production sheet", () => {
  const markdown = workflowMarkdown(buildNameWorkflow(input));
  assert.match(markdown, /Step 1: draw-desire/);
  assert.match(markdown, /Character council/);
  assert.match(markdown, /Ren \| verify first/);
  assert.match(markdown, /reader-humility-loop → character-position/);
});
