"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { NeuralComparisonReport, SimulationState } from "./types";
import type { LinkStatus } from "./useSimulation";

export interface UseNeuralComparisonResult {
  comparison: NeuralComparisonReport | null;
  loading: boolean;
  error: string | null;
  isStale: boolean;
  refresh: () => Promise<void>;
}

export function useNeuralComparison(
  state: SimulationState | null,
  link: LinkStatus,
): UseNeuralComparisonResult {
  const [comparison, setComparison] = useState<NeuralComparisonReport | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchId = useRef(0);
  const stateRef = useRef(state);
  stateRef.current = state;

  const sceneRevision = state?.scene_revision ?? 0;
  const currentProposalId = state?.current_proposal_id ?? null;
  const epoch = state?.epoch ?? 0;

  const fetchComparison = useCallback(async () => {
    if (link === "offline" || !stateRef.current) {
      setLoading(false);
      return;
    }
    const currentId = ++fetchId.current;
    setLoading(true);
    try {
      const res = await api.neuralComparison();
      if (currentId === fetchId.current) {
        setComparison(res);
        setError(null);
      }
    } catch (e: unknown) {
      if (currentId === fetchId.current) {
        setError(e instanceof Error ? e.message : "Failed to load neural comparison");
      }
    } finally {
      if (currentId === fetchId.current) {
        setLoading(false);
      }
    }
  }, [link]);

  // Synchronize on revision or proposal changes
  useEffect(() => {
    if (!stateRef.current) return;
    void fetchComparison();
  }, [sceneRevision, currentProposalId, epoch, fetchComparison]);

  const isStale = Boolean(
    comparison &&
      (comparison.status === "stale" ||
        comparison.context === "stale" ||
        (state && comparison.scene_revision !== state.scene_revision) ||
        (state?.current_proposal_id && comparison.proposal_id !== state.current_proposal_id)),
  );

  return { comparison, loading, error, isStale, refresh: fetchComparison };
}
