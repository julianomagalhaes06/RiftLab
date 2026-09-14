export type Objective = "BALANCED" | "SURVIVAL" | "DAMAGE" | "UTILITY";

export type BudgetSource = "UNKNOWN" | "FRAME_ESTIMATE" | "USER_PROVIDED";

export type AnalysisStatus = "AVAILABLE" | "INSUFFICIENT_EVIDENCE" | "UNSUPPORTED";

export type SupportLevel = "FULL" | "LIMITED";

export interface Budget {
  source: BudgetSource;
  /** Ilustrativo; nunca ouro exato real. */
  approxGold?: number;
  ageSeconds?: number;
  stale?: boolean;
}

export interface Alternative {
  id: string;
  label: string;
  tags: string[];
  remainingCost: number | null;
  /** Texto exibido apenas após reveal; nunca antes. */
  postRevealNote: string;
}

export interface DecisionExample {
  id: string;
  kind: "normal" | "incompatible" | "missing-catalog";
  label: string;
  timeLabel: string;
  participantRole: string;
  levelLabel: string;
  compositionNote: string;
  ownState: string;
  inventorySummary: string[];
  budget: Budget;
  analysisStatus: AnalysisStatus;
  supportLevel: SupportLevel;
  reasonNote?: string;
  alternatives: Alternative[];
  incompatibleReason?: string;
  missingCatalogReason?: string;
}

export interface ParticipantExample {
  id: string;
  label: string;
  decisions: DecisionExample[];
  emptyReason?: string;
}

export interface TransferCase {
  id: string;
  label: string;
  timeLabel: string;
  compositionNote: string;
  ownState: string;
  alternatives: Alternative[];
}

export interface AttemptState {
  decisionId: string;
  objective: Objective | null;
  selectedItemId: string | null;
  skipped: boolean;
  answered: boolean;
  rationale: string;
  hintLevel: 0 | 1 | 2;
  revealedAt: string | null;
  revealedDirect: boolean;
}

export interface GlossaryEntry {
  term: string;
  definition: string;
}
