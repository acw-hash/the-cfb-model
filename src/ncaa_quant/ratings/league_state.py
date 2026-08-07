"""Joint league state for the Stage-1 Kalman filter (DESIGN §9.2–§9.3, audit A-3/B-1).

The filter maintains one mean vector and one full covariance over every FBS
team's latent block plus league-level states. Cross-team covariance is how
schedule information propagates: beating a common opponent updates beliefs about
transitive opponents. Independent per-team filters discard that information.

Layout of the joint vector
--------------------------
``[ team_0 (d) | … | team_{n-1} (d) | hfa_dev_0 | … | hfa_dev_{n-1}
  | hfa_global (1) | scoring_env (1) ]``

``scoring_env`` carries the absolute efficiency level. After every measurement
update, offensive and defensive blocks are projected onto league-mean zero so
the level cannot wander into the unidentified null direction of
``off − def`` (§9.3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from ncaa_quant.ratings.state_space import (
    FCS_TEAM_KEY,
    GaussianState,
    InnovationRecord,
    StateSpaceConfig,
    fcs_pinned_state,
    initial_hfa_global,
    initial_hfa_team,
    initial_team_state,
    kalman_update,
    observation_epa_variance,
    project_league_mean_zero,
)
from ncaa_quant.utils.timeutils import to_utc


class LeagueStateError(ValueError):
    """Raised for joint-league-state contract violations."""


@dataclass
class LeagueGameUpdate:
    """Result of one joint per-game update on a :class:`LeagueState`."""

    innovations: list[InnovationRecord] = field(default_factory=list)
    log_likelihood: float = 0.0


@dataclass
class LeagueState:
    """Single joint Gaussian over the FBS league (§9.2)."""

    config: StateSpaceConfig
    team_ids: list[str] = field(default_factory=list)
    mean: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=float))
    cov: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=float))

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    @property
    def n_teams(self) -> int:
        return len(self.team_ids)

    @property
    def d(self) -> int:
        return int(self.config.n_dims)

    @property
    def dim(self) -> int:
        """Total joint dimension."""
        n = self.n_teams
        if n == 0:
            return 2  # hfa_global + scoring_env alone
        return n * self.d + n + 2

    def _index_of(self, tid: str) -> int:
        try:
            return self.team_ids.index(tid)
        except ValueError as exc:
            msg = f"team {tid!r} is not in the league state"
            raise LeagueStateError(msg) from exc

    def team_slice(self, tid: str) -> slice:
        i = self._index_of(tid)
        d = self.d
        return slice(i * d, (i + 1) * d)

    def hfa_dev_index(self, tid: str) -> int:
        return self.n_teams * self.d + self._index_of(tid)

    def hfa_global_index(self) -> int:
        n = self.n_teams
        return n * self.d + n

    def scoring_env_index(self) -> int:
        return self.hfa_global_index() + 1

    def off_indices(self) -> list[int]:
        if "off_epa" not in self.config.state_dims:
            return []
        i_off = self.config.dim_index("off_epa")
        d = self.d
        return [t * d + i_off for t in range(self.n_teams)]

    def def_indices(self) -> list[int]:
        if "def_epa" not in self.config.state_dims:
            return []
        i_def = self.config.dim_index("def_epa")
        d = self.d
        return [t * d + i_def for t in range(self.n_teams)]

    # ------------------------------------------------------------------
    # Construction / admission
    # ------------------------------------------------------------------

    @classmethod
    def empty(cls, config: StateSpaceConfig) -> LeagueState:
        """League with no teams yet — only ``hfa_global`` and ``scoring_env``."""
        hfa = initial_hfa_global(config)
        mean = np.array(
            [float(hfa.mean[0]), float(config.scoring_env_prior_mean)],
            dtype=float,
        )
        cov = np.diag(
            [float(hfa.cov[0, 0]), float(config.scoring_env_prior_var)]
        ).astype(float)
        return cls(config=config, team_ids=[], mean=mean, cov=cov)

    def has_team(self, tid: str) -> bool:
        return tid in self.team_ids

    def admit(
        self,
        tid: str,
        prior: GaussianState | None = None,
        hfa_dev: GaussianState | None = None,
    ) -> None:
        """Append a team block, expanding the joint mean and covariance."""
        key = str(tid)
        if key in self.team_ids or key == FCS_TEAM_KEY:
            return
        cfg = self.config
        d = self.d
        team_prior = prior if prior is not None else initial_team_state(cfg)
        dev_prior = hfa_dev if hfa_dev is not None else initial_hfa_team(cfg)
        if team_prior.mean.shape[0] != d:
            msg = f"prior dim {team_prior.mean.shape[0]} != configured {d}"
            raise LeagueStateError(msg)

        old_n = self.n_teams
        new_n = old_n + 1
        new_dim = new_n * d + new_n + 2
        new_mean = np.zeros(new_dim, dtype=float)
        new_cov = np.zeros((new_dim, new_dim), dtype=float)

        # Map old positions → new positions. Team blocks stay put; hfa_devs and
        # league states shift right by d+1 (new team block + new hfa_dev).
        old_team_end = old_n * d
        if old_n > 0:
            new_mean[:old_team_end] = self.mean[:old_team_end]
            new_cov[:old_team_end, :old_team_end] = self.cov[:old_team_end, :old_team_end]

            # Old hfa_devs → new hfa_devs (shifted by one slot at the end).
            old_dev_lo = old_team_end
            old_dev_hi = old_team_end + old_n
            new_dev_lo = new_n * d
            new_mean[new_dev_lo : new_dev_lo + old_n] = self.mean[old_dev_lo:old_dev_hi]
            new_cov[
                new_dev_lo : new_dev_lo + old_n,
                new_dev_lo : new_dev_lo + old_n,
            ] = self.cov[old_dev_lo:old_dev_hi, old_dev_lo:old_dev_hi]
            # Cross cov team↔dev
            new_cov[:old_team_end, new_dev_lo : new_dev_lo + old_n] = self.cov[
                :old_team_end, old_dev_lo:old_dev_hi
            ]
            new_cov[new_dev_lo : new_dev_lo + old_n, :old_team_end] = self.cov[
                old_dev_lo:old_dev_hi, :old_team_end
            ]

            # League states (last 2 of old).
            old_hfa_i = old_dev_hi
            old_env_i = old_dev_hi + 1
            new_hfa_i = new_n * d + new_n
            new_env_i = new_hfa_i + 1
            for old_i, new_i in ((old_hfa_i, new_hfa_i), (old_env_i, new_env_i)):
                new_mean[new_i] = self.mean[old_i]
                new_cov[new_i, new_i] = self.cov[old_i, old_i]
                # Cross with old teams
                new_cov[:old_team_end, new_i] = self.cov[:old_team_end, old_i]
                new_cov[new_i, :old_team_end] = self.cov[old_i, :old_team_end]
                # Cross with old hfa_devs
                new_cov[new_dev_lo : new_dev_lo + old_n, new_i] = self.cov[
                    old_dev_lo:old_dev_hi, old_i
                ]
                new_cov[new_i, new_dev_lo : new_dev_lo + old_n] = self.cov[
                    old_i, old_dev_lo:old_dev_hi
                ]
            # Cross between the two league states
            new_cov[new_hfa_i, new_env_i] = self.cov[old_hfa_i, old_env_i]
            new_cov[new_env_i, new_hfa_i] = self.cov[old_env_i, old_hfa_i]
        else:
            # Empty → just the two league states were stored.
            new_hfa_i = d + 1
            new_env_i = d + 2
            new_mean[new_hfa_i] = self.mean[0]
            new_mean[new_env_i] = self.mean[1]
            new_cov[new_hfa_i, new_hfa_i] = self.cov[0, 0]
            new_cov[new_env_i, new_env_i] = self.cov[1, 1]
            new_cov[new_hfa_i, new_env_i] = self.cov[0, 1]
            new_cov[new_env_i, new_hfa_i] = self.cov[1, 0]

        # Insert the new team at the end of the team block.
        new_team_lo = old_n * d
        new_mean[new_team_lo : new_team_lo + d] = team_prior.mean
        new_cov[new_team_lo : new_team_lo + d, new_team_lo : new_team_lo + d] = team_prior.cov

        # New hfa_dev at the end of the hfa_dev block.
        new_dev_i = new_n * d + old_n
        new_mean[new_dev_i] = float(dev_prior.mean[0])
        new_cov[new_dev_i, new_dev_i] = float(dev_prior.cov[0, 0])

        self.team_ids.append(key)
        self.mean = new_mean
        self.cov = 0.5 * (new_cov + new_cov.T)

    def ensure(
        self,
        tid: str,
        *,
        prior: GaussianState | None = None,
        hfa_dev: GaussianState | None = None,
    ) -> None:
        if not self.has_team(str(tid)):
            self.admit(str(tid), prior=prior, hfa_dev=hfa_dev)

    # ------------------------------------------------------------------
    # Marginals / process noise
    # ------------------------------------------------------------------

    def team_marginal(self, tid: str) -> GaussianState:
        sl = self.team_slice(str(tid))
        return GaussianState(mean=self.mean[sl].copy(), cov=self.cov[sl, sl].copy())

    def hfa_dev_marginal(self, tid: str) -> GaussianState:
        i = self.hfa_dev_index(str(tid))
        return GaussianState(
            mean=np.array([self.mean[i]]),
            cov=np.array([[self.cov[i, i]]]),
        )

    def hfa_global_marginal(self) -> GaussianState:
        i = self.hfa_global_index()
        return GaussianState(
            mean=np.array([self.mean[i]]),
            cov=np.array([[self.cov[i, i]]]),
        )

    def scoring_env_marginal(self) -> GaussianState:
        i = self.scoring_env_index()
        return GaussianState(
            mean=np.array([self.mean[i]]),
            cov=np.array([[self.cov[i, i]]]),
        )

    def add_process_noise_block(self, sl: slice, q: np.ndarray) -> None:
        self.cov[sl, sl] = self.cov[sl, sl] + q

    def add_process_noise_scalar(self, index: int, q: float) -> None:
        self.cov[index, index] = self.cov[index, index] + float(q)

    def set_team_state(self, tid: str, state: GaussianState) -> None:
        """Replace a team's marginal (e.g. season-boundary prior injection).

        Cross-covariance between this team and the rest of the league is zeroed —
        a new season's prior is independent of last season's transitive beliefs
        once the soft regression / prior blend has been applied.
        """
        sl = self.team_slice(str(tid))
        self.mean[sl] = state.mean
        self.cov[sl, :] = 0.0
        self.cov[:, sl] = 0.0
        self.cov[sl, sl] = state.cov

    def set_hfa_dev(self, tid: str, state: GaussianState) -> None:
        i = self.hfa_dev_index(str(tid))
        self.mean[i] = float(state.mean[0])
        self.cov[i, :] = 0.0
        self.cov[:, i] = 0.0
        self.cov[i, i] = float(state.cov[0, 0])

    def project_identifiability(self) -> None:
        """League-mean-zero projection on off and def (§9.3)."""
        if self.n_teams < 2:
            return
        index_sets = [self.off_indices(), self.def_indices()]
        index_sets = [g for g in index_sets if len(g) >= 2]
        if not index_sets:
            return
        projected = project_league_mean_zero(
            GaussianState(mean=self.mean, cov=self.cov),
            index_sets,
        )
        self.mean = projected.mean
        self.cov = projected.cov

    def joint_team_block(self, home: str, away: str) -> tuple[np.ndarray, np.ndarray]:
        """Mean and covariance of the two teams' blocks (for epistemic draws)."""
        h_sl = self.team_slice(str(home))
        a_sl = self.team_slice(str(away))
        idx = list(range(h_sl.start, h_sl.stop)) + list(range(a_sl.start, a_sl.stop))
        mean = self.mean[idx].copy()
        cov = self.cov[np.ix_(idx, idx)].copy()
        return mean, cov

    # ------------------------------------------------------------------
    # Measurement update
    # ------------------------------------------------------------------

    def update_game(
        self,
        home_id: Any,
        away_id: Any,
        *,
        home_epa: float | None,
        away_epa: float | None,
        home_plays: float,
        away_plays: float,
        home_st_epa: float | None = None,
        away_st_epa: float | None = None,
        pace_obs: float | None = None,
        margin: float | None = None,
        neutral_site: bool = False,
        home_is_fcs: bool = False,
        away_is_fcs: bool = False,
        game_id: int = 0,
        season: int = 0,
        week: int = 0,
        event_time: datetime | None = None,
    ) -> LeagueGameUpdate:
        """Joint Kalman update for one game; projects identifiability after."""
        cfg = self.config
        d = self.d
        h_key = FCS_TEAM_KEY if home_is_fcs else str(home_id)
        a_key = FCS_TEAM_KEY if away_is_fcs else str(away_id)

        if not home_is_fcs:
            self.ensure(h_key)
        if not away_is_fcs:
            self.ensure(a_key)

        i_off = cfg.dim_index("off_epa") if "off_epa" in cfg.state_dims else None
        i_def = cfg.dim_index("def_epa") if "def_epa" in cfg.state_dims else None
        i_st = cfg.dim_index("st_value") if "st_value" in cfg.state_dims else None
        i_pace = cfg.dim_index("pace") if "pace" in cfg.state_dims else None

        fcs = fcs_pinned_state(cfg)
        n = self.dim
        rows: list[np.ndarray] = []
        ys: list[float] = []
        rs: list[float] = []
        names: list[str] = []
        team_for_obs: list[Any] = []

        def _row() -> np.ndarray:
            return np.zeros(n, dtype=float)

        def _team_base(tid: str) -> int:
            return self._index_of(tid) * d

        # Predicted contribution from a fixed FCS opponent (not in the state).
        fcs_def = float(fcs.mean[i_def]) if i_def is not None else 0.0
        fcs_var = float(fcs.cov[i_def, i_def]) if i_def is not None else cfg.fcs_prior_var

        hfa_i = self.hfa_global_index()
        env_i = self.scoring_env_index()

        if home_epa is not None and i_off is not None and i_def is not None:
            h = _row()
            y = float(home_epa)
            r = observation_epa_variance(home_plays, cfg)
            if home_is_fcs:
                # Observing FCS offense against our defense — rare; skip update
                # of named-school state from FCS offense alone.
                pass
            else:
                h[_team_base(h_key) + i_off] = 1.0
                if away_is_fcs:
                    # y ≈ off_h - fcs_def + hfa + env  ⇒  move fcs_def into y
                    y = y + fcs_def
                    r = r + fcs_var
                else:
                    h[_team_base(a_key) + i_def] = -1.0
                if not neutral_site:
                    h[hfa_i] = 1.0
                    h[self.hfa_dev_index(h_key)] = 1.0
                h[env_i] = 1.0
                rows.append(h)
                ys.append(y)
                rs.append(r)
                names.append("home_epa")
                team_for_obs.append(home_id)

        if away_epa is not None and i_off is not None and i_def is not None:
            h = _row()
            y = float(away_epa)
            r = observation_epa_variance(away_plays, cfg)
            if away_is_fcs:
                pass
            else:
                h[_team_base(a_key) + i_off] = 1.0
                if home_is_fcs:
                    y = y + fcs_def
                    r = r + fcs_var
                else:
                    h[_team_base(h_key) + i_def] = -1.0
                h[env_i] = 1.0
                rows.append(h)
                ys.append(y)
                rs.append(r)
                names.append("away_epa")
                team_for_obs.append(away_id)

        if (
            home_st_epa is not None
            and away_st_epa is not None
            and i_st is not None
            and not home_is_fcs
            and not away_is_fcs
            and not (math.isnan(home_st_epa) or math.isnan(away_st_epa))
        ):
            h = _row()
            h[_team_base(h_key) + i_st] = 1.0
            h[_team_base(a_key) + i_st] = -1.0
            rows.append(h)
            ys.append(float(home_st_epa) - float(away_st_epa))
            rs.append(cfg.r_st_base**2)
            names.append("st_diff")
            team_for_obs.append(home_id)

        if (
            pace_obs is not None
            and i_pace is not None
            and not math.isnan(pace_obs)
            and not home_is_fcs
            and not away_is_fcs
        ):
            y_pace = float(pace_obs) / cfg.ref_plays - 1.0
            h = _row()
            h[_team_base(h_key) + i_pace] = 0.5
            h[_team_base(a_key) + i_pace] = 0.5
            rows.append(h)
            ys.append(y_pace)
            rs.append(cfg.r_pace_base**2)
            names.append("pace")
            team_for_obs.append(home_id)

        if (
            margin is not None
            and i_off is not None
            and i_def is not None
            and not math.isnan(margin)
            and not home_is_fcs
            and not away_is_fcs
        ):
            # Margin is a difference — scoring_env cancels and is omitted.
            h = _row()
            h[_team_base(h_key) + i_off] = cfg.margin_scale
            h[_team_base(a_key) + i_def] = -cfg.margin_scale
            h[_team_base(a_key) + i_off] = -cfg.margin_scale
            h[_team_base(h_key) + i_def] = cfg.margin_scale
            if not neutral_site:
                h[hfa_i] = cfg.margin_scale
                h[self.hfa_dev_index(h_key)] = cfg.margin_scale
            rows.append(h)
            ys.append(float(margin))
            rs.append(cfg.r_margin**2)
            names.append("margin")
            team_for_obs.append(home_id)

        ts = event_time if event_time is not None else datetime(1970, 1, 1, tzinfo=UTC)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        ts = to_utc(ts)

        if not rows:
            return LeagueGameUpdate()

        h_mat = np.vstack(rows)
        y_vec = np.asarray(ys, dtype=float)
        r_vec = np.asarray(rs, dtype=float)
        prior = GaussianState(mean=self.mean, cov=self.cov)
        posterior, innov_raw, _used, z, ll = kalman_update(
            prior,
            h_mat,
            y_vec,
            r_vec,
            winsor_sigma=cfg.residual_winsor_sigma,
        )
        self.mean = posterior.mean
        self.cov = posterior.cov
        self.project_identifiability()

        s = h_mat @ prior.cov @ h_mat.T + np.diag(r_vec)
        pred_sd = np.sqrt(np.clip(np.diag(s), 1e-18, None))
        innovations = [
            InnovationRecord(
                game_id=int(game_id),
                team_id=team_for_obs[i],
                season=int(season),
                week=int(week),
                event_time=ts,
                obs_name=names[i],
                innovation=float(innov_raw[i]),
                pred_sd=float(pred_sd[i]),
                z=float(z[i]),
                winsorized=bool(abs(float(z[i])) > cfg.residual_winsor_sigma),
            )
            for i in range(len(names))
        ]
        return LeagueGameUpdate(innovations=innovations, log_likelihood=ll)


__all__ = [
    "LeagueGameUpdate",
    "LeagueState",
    "LeagueStateError",
]
