// Shapes of the agent's routes and stream (docs/visualizer-agent.md §2–§4).
import type { UIMessage } from 'ai';
import type { ViewSpec } from './types';

export type HarnessName = 'claude' | 'codex';
export type Harness = { name: HarnessName; installed: boolean; signedIn: boolean; version: string | null };
export type Login = {
  state: 'starting' | 'open-url' | 'enter-code' | 'show-code' | 'done' | 'failed';
  url: string | null; userCode: string | null; message: string | null;
};

// What every chat request carries besides the AI SDK's own fields (§3).
export type AgentView = { spec: ViewSpec; compileId: string | null; picked: string[] };

// The `data-view` chunk (§4): a view the agent applied, or went back to.
export type ViewEvent = { spec: ViewSpec; summary: string; undoOf: string | null };
export type ChatMessage = UIMessage<unknown, { view: ViewEvent }>;

export const HARNESS_LABEL: Record<HarnessName, string> = { claude: 'Claude', codex: 'Codex' };
