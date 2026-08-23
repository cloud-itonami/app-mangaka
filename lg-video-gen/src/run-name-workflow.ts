#!/usr/bin/env node
import * as fs from "node:fs";
import * as path from "node:path";
import { buildNameWorkflow, workflowMarkdown, type NameWorkflowInput } from "./name-character-workflow.js";

const args = process.argv.slice(2);
const value = (name: string): string | undefined => {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : undefined;
};
const inputPath = value("--input");
const outDir = value("--out") ?? "out/name-workflow";
if (!inputPath) throw new Error("provide --input <workflow.json>");
const input = JSON.parse(fs.readFileSync(path.resolve(inputPath), "utf8")) as NameWorkflowInput;
const workflow = buildNameWorkflow(input);
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, "name-character-workflow.json"), JSON.stringify(workflow, null, 2) + "\n");
fs.writeFileSync(path.join(outDir, "name-character-workflow.md"), workflowMarkdown(workflow));
console.log(`done: ${outDir} (${workflow.steps.length} steps, ${workflow.characterCouncil.length} characters)`);
