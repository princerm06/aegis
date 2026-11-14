"""PyTorch neural assignment scoring models: Supervised MLP and Bipartite GNN.

Both models predict per-pair assignment logits s_{i,j} from pre-assignment
scene features. Neither model ever authorizes dispatch or overrides hard
feasibility constraints.
"""

from __future__ import annotations

import torch
import torch.nn as nn

from neural_assignment.features import (
    CASUALTY_NODE_FEATURE_DIM,
    PAIR_FEATURE_DIM,
    RESPONDER_NODE_FEATURE_DIM,
)


class PairwiseAssignmentMLP(nn.Module):
    """Pairwise assignment scorer with residual hidden block and scene context features."""

    def __init__(
        self,
        in_dim: int = PAIR_FEATURE_DIM,
        hidden_dim: int = 128,
        mid_dim: int = 64,
        dropout: float = 0.08,
    ) -> None:
        super().__init__()
        self.in_dim = in_dim
        self.hidden_dim = hidden_dim
        self.mid_dim = mid_dim

        self.input_proj = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )
        self.res_block = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
        )
        self.act = nn.GELU()
        self.head = nn.Sequential(
            nn.Linear(hidden_dim, mid_dim),
            nn.LayerNorm(mid_dim),
            nn.GELU(),
            nn.Linear(mid_dim, 1),
        )

    def forward(
        self,
        pair_features: torch.Tensor,
        casualty_node_features: torch.Tensor | None = None,
        responder_node_features: torch.Tensor | None = None,
        feasible_mask: torch.Tensor | None = None,
        valid_pair_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Compute assignment logits of shape (..., C, R)."""
        h = self.input_proj(pair_features)
        h = self.act(h + self.res_block(h))
        logits = self.head(h).squeeze(-1)
        return logits


class _BipartiteMessageLayer(nn.Module):
    """One round of bipartite message passing over feasible casualty-responder edges."""

    def __init__(self, hidden_dim: int, dropout: float = 0.08) -> None:
        super().__init__()
        self.edge_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.edge_norm = nn.LayerNorm(hidden_dim)

        self.cas_attn = nn.Linear(hidden_dim, 1)
        self.res_attn = nn.Linear(hidden_dim, 1)

        self.cas_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.cas_norm = nn.LayerNorm(hidden_dim)

        self.res_mlp = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, hidden_dim),
        )
        self.res_norm = nn.LayerNorm(hidden_dim)

    def forward(
        self,
        h_cas: torch.Tensor,  # (B, C, H)
        h_res: torch.Tensor,  # (B, R, H)
        h_edge: torch.Tensor,  # (B, C, R, H)
        edge_mask: torch.Tensor,  # (B, C, R) bool
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        bsz, n_cas, n_res, hdim = h_edge.shape
        cas_exp = h_cas.unsqueeze(2).expand(bsz, n_cas, n_res, hdim)
        res_exp = h_res.unsqueeze(1).expand(bsz, n_cas, n_res, hdim)

        edge_in = torch.cat([h_edge, cas_exp, res_exp], dim=-1)
        h_edge = self.edge_norm(h_edge + self.edge_mlp(edge_in))

        mask_f = edge_mask.unsqueeze(-1).to(h_edge.dtype)  # (B, C, R, 1)

        # Aggregate responder options for each casualty across dimension R (dim=2)
        cas_has_edges = edge_mask.any(dim=2, keepdim=True).unsqueeze(-1)  # (B, C, 1, 1)
        cas_scores = self.cas_attn(h_edge).masked_fill(~edge_mask.unsqueeze(-1), -1e9)
        cas_weights = torch.softmax(cas_scores, dim=2) * mask_f
        cas_attn_pool = (cas_weights * h_edge).sum(dim=2)  # (B, C, H)

        masked_for_cas_max = h_edge.masked_fill(~edge_mask.unsqueeze(-1), -1e4)
        cas_max_pool = masked_for_cas_max.max(dim=2).values
        cas_max_pool = torch.where(
            cas_has_edges.squeeze(-1), cas_max_pool, torch.zeros_like(cas_max_pool)
        )

        h_cas = self.cas_norm(
            h_cas + self.cas_mlp(torch.cat([h_cas, cas_attn_pool, cas_max_pool], dim=-1))
        )

        # Aggregate competing casualties for each responder across dimension C (dim=1)
        res_has_edges = edge_mask.any(dim=1, keepdim=True).unsqueeze(-1)  # (B, 1, R, 1)
        res_scores = self.res_attn(h_edge).masked_fill(~edge_mask.unsqueeze(-1), -1e9)
        res_weights = torch.softmax(res_scores, dim=1) * mask_f
        res_attn_pool = (res_weights * h_edge).sum(dim=1)  # (B, R, H)

        masked_for_res_max = h_edge.masked_fill(~edge_mask.unsqueeze(-1), -1e4)
        res_max_pool = masked_for_res_max.max(dim=1).values
        res_max_pool = torch.where(
            res_has_edges.squeeze(1), res_max_pool, torch.zeros_like(res_max_pool)
        )

        h_res = self.res_norm(
            h_res + self.res_mlp(torch.cat([h_res, res_attn_pool, res_max_pool], dim=-1))
        )

        return h_cas, h_res, h_edge, cas_attn_pool, res_attn_pool


class BipartiteAssignmentGNN(nn.Module):
    """Compact Bipartite Graph Neural Network over casualties, responders, and feasible edges."""

    def __init__(
        self,
        cas_dim: int = CASUALTY_NODE_FEATURE_DIM,
        res_dim: int = RESPONDER_NODE_FEATURE_DIM,
        edge_dim: int = PAIR_FEATURE_DIM,
        hidden_dim: int = 96,
        num_layers: int = 3,
        dropout: float = 0.08,
    ) -> None:
        super().__init__()
        self.cas_dim = cas_dim
        self.res_dim = res_dim
        self.edge_dim = edge_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers

        self.cas_encoder = nn.Sequential(
            nn.Linear(cas_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )
        self.res_encoder = nn.Sequential(
            nn.Linear(res_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )
        self.edge_encoder = nn.Sequential(
            nn.Linear(edge_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )
        self.layers = nn.ModuleList(
            [_BipartiteMessageLayer(hidden_dim, dropout=dropout) for _ in range(num_layers)]
        )
        self.readout = nn.Sequential(
            nn.Linear(hidden_dim * 5, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(
        self,
        pair_features: torch.Tensor,
        casualty_node_features: torch.Tensor,
        responder_node_features: torch.Tensor,
        feasible_mask: torch.Tensor,
        valid_pair_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Compute assignment logits of shape (B, C, R) or (C, R)."""
        unbatched = pair_features.dim() == 3
        if unbatched:
            pair_features = pair_features.unsqueeze(0)
            casualty_node_features = casualty_node_features.unsqueeze(0)
            responder_node_features = responder_node_features.unsqueeze(0)
            feasible_mask = feasible_mask.unsqueeze(0)
            if valid_pair_mask is not None:
                valid_pair_mask = valid_pair_mask.unsqueeze(0)

        edge_mask = feasible_mask.bool()
        if valid_pair_mask is not None:
            edge_mask = edge_mask & valid_pair_mask.bool()

        h_cas = self.cas_encoder(casualty_node_features)
        h_res = self.res_encoder(responder_node_features)
        h_edge = self.edge_encoder(pair_features)

        cas_context = torch.zeros_like(h_cas)
        res_context = torch.zeros_like(h_res)
        for layer in self.layers:
            h_cas, h_res, h_edge, cas_context, res_context = layer(
                h_cas, h_res, h_edge, edge_mask
            )

        bsz, n_cas, n_res, hdim = h_edge.shape
        cas_exp = h_cas.unsqueeze(2).expand(bsz, n_cas, n_res, hdim)
        res_exp = h_res.unsqueeze(1).expand(bsz, n_cas, n_res, hdim)
        cas_diff = h_edge - cas_context.unsqueeze(2).expand(bsz, n_cas, n_res, hdim)
        res_diff = h_edge - res_context.unsqueeze(1).expand(bsz, n_cas, n_res, hdim)

        readout_in = torch.cat([h_edge, cas_exp, res_exp, cas_diff, res_diff], dim=-1)
        logits = self.readout(readout_in).squeeze(-1)

        if unbatched:
            return logits.squeeze(0)
        return logits
