// Shapes of the agent's routes and stream (docs/visualizer-agent.md §2–§4, §7).
import type { UIMessage } from 'ai';
import type { ViewSpec } from './types';

export type HarnessName = 'claude' | 'codex';
export type ModelChoice = { id: string; label: string };
export type Harness = {
  name: HarnessName; installed: boolean; signedIn: boolean; version: string | null;
  // §7: what the page may choose; `default` is the harness's own choice. Absent from an older service.
  models?: ModelChoice[]; defaultModel?: string | null; efforts?: string[]; defaultEffort?: string | null;
};
export type Login = {
  state: 'starting' | 'open-url' | 'enter-code' | 'show-code' | 'done' | 'failed';
  url: string | null; userCode: string | null; message: string | null;
};

// What every chat request carries besides the AI SDK's own fields (§3, §7). With no diagram on the page,
// `owner` and `spec` are null.
export type AgentView = { owner: string | null; spec: ViewSpec | null; compileId: string | null; picked: string[] };
// The model and effort for this turn and the following ones (§7); null is the harness's own choice.
export type AgentChoice = { model: string | null; effort: string | null };

// The `data-view` chunk (§4, §7): a view the agent applied, or went back to, over `owner`'s graphs.
export type ViewEvent = { spec: ViewSpec; summary: string; undoOf: string | null; owner?: string | null };
// The `message-metadata` chunk (§7): which harness and model answered.
export type AnswerMeta = { harness?: string; model?: string | null };
export type ChatMessage = UIMessage<AnswerMeta, { view: ViewEvent }>;

export const HARNESS_LABEL: Record<HarnessName, string> = { claude: 'Claude', codex: 'Codex' };
