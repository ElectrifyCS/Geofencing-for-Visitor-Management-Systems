"""
multilateration.py — Anchor-based (x, y, z) positioning from ranging data.

This is the layer that replaces raw GPS as the Kalman filter's input:
fixed BLE/UWB anchors with known positions, each reporting a distance
(or RSSI) to a tag, solved down to a single (x, y, z) fix per update.

Math foundation (IB AA HL tie-ins):
  - rssi_to_distance(): log-distance path-loss model, a direct application
    of exponential/logarithmic functions.
  - multilaterate(): each anchor gives |P - A_i| = d_i, a sphere in 3-space
    centred at A_i. Subtracting the equation for a reference anchor from
    every other one cancels the quadratic (x^2+y^2+z^2) term, leaving a
    system of *linear* equations in (x, y, z) — systems of linear
    equations / vectors, solved by least squares when there are more
    anchors than the strict minimum (4, for an unambiguous 3D fix).
  - Outlier rejection: robust statistics (median/MAD), the same tool
    UserBehaviorProfile (models.py) already uses to keep one bad reading
    from corrupting the very statistic used to judge it — mean/stddev
    would be corrupted by the outlier itself; median/MAD isn't.
  - Condition number: the weighted design matrix's condition number is
    a direct linear-algebra measure of how geometrically well-determined
    the solve is (dilution of precision) — anchors spread widely produce
    a well-conditioned system; anchors bunched together or nearly
    collinear (a narrow corridor) produce an ill-conditioned one, and
    the *same* ranging noise turns into much larger position error.

Depends only on numpy, consistent with the existing kalman.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

Point3D = Tuple[float, float, float]


def rssi_to_distance(rssi_dbm: float, tx_power_dbm: float, path_loss_exponent: float = 2.0) -> float:
    """
    Log-distance path-loss model: d = 10^((TxPower - RSSI) / (10 * n))

    tx_power_dbm: the RSSI expected at 1 metre from the anchor (a per-anchor
                  calibration constant, not a hardware spec sheet number —
                  measure it on site).
    path_loss_exponent: ~2.0 free space, higher (2.5-4+) in cluttered indoor
                  environments with walls/furniture attenuating signal.
    """
    return 10.0 ** ((tx_power_dbm - rssi_dbm) / (10.0 * path_loss_exponent))


@dataclass(frozen=True)
class Anchor:
    anchor_id: str
    position: Point3D  # fixed, surveyed (x, y, z) in metres


@dataclass(frozen=True)
class Ranging:
    anchor_id: str
    distance_m: float
    # Optional per-reading uncertainty (e.g. derived from RSSI variance).
    # Anchors with tighter uncertainty get weighted more heavily in the solve.
    sigma_m: float = 1.0


@dataclass(frozen=True)
class MultilaterationResult:
    """
    The solve's answer, plus enough about *how* it got there for a caller
    to decide how much to trust it — not just a bare (x, y, z).
    """
    position: Point3D
    condition_number: float          # DOP proxy: higher = geometrically weaker fix
    used_anchor_ids: List[str]       # anchors that contributed to the final solve
    rejected_anchor_ids: List[str]   # anchors dropped as probable multipath/NLOS outliers
    residuals_m: Dict[str, float]    # final |P - A_i| - d_i per USED anchor


def _multilaterate_linear(
    anchors: Sequence[Anchor], rangings: Sequence[Ranging]
) -> Optional[Tuple[Point3D, float]]:
    """
    Closed-form linear solve. Fast, needs no initial guess, but noise-
    sensitive: it works with *squared* distances (di^2 - d0^2), so a
    range error sigma at a distance d contributes roughly 2*d*sigma of
    noise into the linear system before the geometry solve even happens.
    Used as the starting point for the nonlinear refinement below, not
    as the final answer on its own.

    Returns (position, condition_number) — the weighted design matrix's
    condition number, a direct measure of how well the anchor geometry
    determines the solution, independent of ranging noise. Returned here
    since this is where the matrix already exists; callers use it to
    gate confidence on the final fix (see multilaterate() below).
    """
    by_id = {a.anchor_id: a for a in anchors}
    usable = [r for r in rangings if r.anchor_id in by_id]

    if len(usable) < 4:
        return None  # not enough anchors in range for a 3D fix

    ref = usable[0]
    x0, y0, z0 = by_id[ref.anchor_id].position
    d0 = ref.distance_m

    rows: List[List[float]] = []
    rhs: List[float] = []
    weights: List[float] = []

    for r in usable[1:]:
        xi, yi, zi = by_id[r.anchor_id].position
        di = r.distance_m

        # 2(x0-xi)x + 2(y0-yi)y + 2(z0-zi)z = (di^2 - d0^2) - (xi^2-x0^2) - (yi^2-y0^2) - (zi^2-z0^2)
        rows.append([2 * (x0 - xi), 2 * (y0 - yi), 2 * (z0 - zi)])
        rhs.append(
            (di**2 - d0**2)
            - (xi**2 - x0**2)
            - (yi**2 - y0**2)
            - (zi**2 - z0**2)
        )
        # Combine both readings' uncertainty; tighter combined sigma -> higher weight.
        combined_sigma = math.sqrt(r.sigma_m**2 + ref.sigma_m**2)
        weights.append(1.0 / max(combined_sigma, 1e-6))

    A = np.array(rows, dtype=float)
    b = np.array(rhs, dtype=float)
    sqrt_w = np.sqrt(np.array(weights, dtype=float))

    # Weighted least squares, solved directly on the weighted design matrix
    # (A_w = diag(sqrt_w) @ A) rather than via the normal equations
    # (A^T W A). Forming A^T A squares the matrix's condition number --
    # with anchors bunched close together this system is already
    # moderately ill-conditioned (cond(A) ~ 200-300 for a typical room
    # layout), and squaring that turns 15cm of ranging noise into metres
    # of position error. Solving the weighted system directly via lstsq's
    # QR decomposition avoids that amplification entirely.
    A_weighted = A * sqrt_w[:, np.newaxis]
    b_weighted = b * sqrt_w
    try:
        solution, *_ = np.linalg.lstsq(A_weighted, b_weighted, rcond=None)
        condition_number = float(np.linalg.cond(A_weighted))
    except np.linalg.LinAlgError:
        return None

    position = (float(solution[0]), float(solution[1]), float(solution[2]))
    return position, condition_number


def _refine_nonlinear(
    initial_guess: Point3D,
    anchors: Sequence[Anchor],
    rangings: Sequence[Ranging],
    max_iterations: int = 15,
    tolerance_m: float = 1e-4,
) -> Point3D:
    """
    Gauss-Newton refinement on the true residual r_i(P) = |P - A_i| - d_i,
    minimizing sum(w_i * r_i^2) directly -- unlike the linear solve, this
    works with distances themselves, not their squares, so ranging noise
    isn't amplified by the differencing step. The linear solve's output
    is only used as the starting point; a few iterations from here
    converge to the true nonlinear least-squares minimum.
    """
    by_id = {a.anchor_id: a for a in anchors}
    usable = [r for r in rangings if r.anchor_id in by_id]

    P = np.array(initial_guess, dtype=float)

    for _ in range(max_iterations):
        rows = []
        residuals = []
        weights = []
        for r in usable:
            A_pos = np.array(by_id[r.anchor_id].position, dtype=float)
            delta = P - A_pos
            predicted_d = np.linalg.norm(delta)
            if predicted_d < 1e-9:
                continue
            unit_vector = delta / predicted_d  # Jacobian row: d(|P-A_i|)/dP
            rows.append(unit_vector)
            residuals.append(predicted_d - r.distance_m)
            weights.append(1.0 / max(r.sigma_m, 1e-6) ** 2)

        J = np.array(rows)
        r_vec = np.array(residuals)
        W = np.diag(weights)

        # Gauss-Newton step: solve (J^T W J) dP = -J^T W r
        JtW = J.T @ W
        try:
            step, *_ = np.linalg.lstsq(JtW @ J, -JtW @ r_vec, rcond=None)
        except np.linalg.LinAlgError:
            break

        P = P + step
        if np.linalg.norm(step) < tolerance_m:
            break

    return float(P[0]), float(P[1]), float(P[2])


def _residuals_m(position: Point3D, anchors: Sequence[Anchor], rangings: Sequence[Ranging]) -> Dict[str, float]:
    """Absolute |P - A_i| - d_i for every ranging whose anchor is known."""
    by_id = {a.anchor_id: a for a in anchors}
    return {
        r.anchor_id: abs(math.dist(position, by_id[r.anchor_id].position) - r.distance_m)
        for r in rangings
        if r.anchor_id in by_id
    }


def multilaterate(
    anchors: Sequence[Anchor],
    rangings: Sequence[Ranging],
    outlier_k: float = 3.0,
    max_rejection_rounds: int = 2,
) -> Optional[MultilaterationResult]:
    """
    Solve for tag (x, y, z) from N anchor distance measurements: a fast
    closed-form linear solve for an initial estimate, immediately refined
    by a few Gauss-Newton iterations against the true (non-squared)
    distance residuals. Requires at least 4 anchors, not all at the same
    height (see the __main__ demo below for why).

    After solving, checks whether any single anchor's final residual is
    an outlier relative to the others — median + outlier_k * (MAD, scaled
    to a consistent sigma estimate) — the signature of a multipath/NLOS
    reading that ranging noise alone doesn't explain. If so, drops the
    single worst offender and re-solves from scratch (geometry changes
    enough removing one anchor that a second flagged anchor may resolve
    on its own, rather than trusting one noisy pass to get every
    rejection right at once), up to max_rejection_rounds times. Never
    rejects below 4 anchors — a degraded fix beats no fix.
    """
    current_rangings = list(rangings)
    rejected: List[str] = []

    for _ in range(max_rejection_rounds + 1):
        linear = _multilaterate_linear(anchors, current_rangings)
        if linear is None:
            return None
        linear_position, condition_number = linear
        position = _refine_nonlinear(linear_position, anchors, current_rangings)
        residuals = _residuals_m(position, anchors, current_rangings)
        used_ids = list(residuals.keys())

        if len(used_ids) <= 4:
            return MultilaterationResult(
                position=position, condition_number=condition_number,
                used_anchor_ids=used_ids, rejected_anchor_ids=rejected,
                residuals_m=residuals,
            )

        values = np.array(list(residuals.values()))
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median))) + 1e-6
        robust_sigma = 1.4826 * mad  # standard MAD -> sigma consistency factor under normality
        threshold = median + outlier_k * robust_sigma

        outliers = [aid for aid, v in residuals.items() if v > threshold]
        if not outliers:
            return MultilaterationResult(
                position=position, condition_number=condition_number,
                used_anchor_ids=used_ids, rejected_anchor_ids=rejected,
                residuals_m=residuals,
            )

        worst = max(outliers, key=lambda aid: residuals[aid])
        rejected.append(worst)
        current_rangings = [r for r in current_rangings if r.anchor_id != worst]

    # Ran out of rejection rounds — return the last computed solve rather
    # than looping forever on a persistently disagreeing anchor set.
    return MultilaterationResult(
        position=position, condition_number=condition_number,
        used_anchor_ids=used_ids, rejected_anchor_ids=rejected,
        residuals_m=residuals,
    )


if __name__ == "__main__":
    # Demo: 5 ceiling-mounted anchors around a 20m x 15m room, tag near
    # the middle of the floor. Ranges computed from true geometry, then
    # perturbed with realistic UWB-scale noise (~10-20cm) to show the
    # solver is robust to it, not just exact-case correct.

    rng = np.random.default_rng(seed=7)

    # NOTE: anchor heights are deliberately varied, not all ceiling-mounted.
    # If every anchor sits at the same z, the (z0 - zi) term is zero in
    # every linearized equation and z becomes unobservable -- the solver
    # can't be blamed for guessing wrong when the geometry gives it no
    # information to work with. Mixing ceiling + floor/wall-height anchors
    # is what actually makes elevation solvable.
    anchors = [
        Anchor("A1", (0.0, 0.0, 3.0)),    # ceiling corner
        Anchor("A2", (20.0, 0.0, 3.0)),   # ceiling corner
        Anchor("A3", (20.0, 15.0, 0.3)),  # skirting/floor-height corner
        Anchor("A4", (0.0, 15.0, 0.3)),   # skirting/floor-height corner
        Anchor("A5", (10.0, 7.5, 1.5)),   # mid-height, centre of room
    ]

    true_position: Point3D = (12.3, 6.1, 1.4)  # tag carried at hip height

    rangings = []
    for a in anchors:
        true_d = math.dist(true_position, a.position)
        noisy_d = true_d + rng.normal(0, 0.15)  # 15cm stddev, typical UWB
        rangings.append(Ranging(anchor_id=a.anchor_id, distance_m=noisy_d, sigma_m=0.15))

    result = multilaterate(anchors, rangings)
    error = math.dist(true_position, result.position)

    print(f"True position:      {true_position}")
    print(f"Estimated position: {tuple(round(v, 3) for v in result.position)}")
    print(f"Error:               {error * 100:.1f} cm")
    print(f"Condition number:    {result.condition_number:.1f} (well-spread anchors -> low)")
    print(f"Anchors used:        {result.used_anchor_ids} (none rejected, as expected: no bad reading here)")
    print(
        "\nNOTE: over many noise draws, horizontal (x, y) error for this "
        "layout runs ~30cm RMS but vertical (z) error runs ~130cm+ RMS -- "
        "roughly 5x worse. That's not a bug, it's geometry: these anchors "
        "span ~2.7m vertically but ~20m+ horizontally, so z is much less "
        "observable than x/y (the same reason GPS altitude is noisier than "
        "GPS position -- all the satellites are 'above' you).\n"
        "Practical takeaway for floor differentiation specifically: don't "
        "trust raw trilaterated z to separate ~3m floor slabs. Use\n"
        "per-floor anchor networks instead -- floors attenuate RF hard "
        "enough that 'which anchors can hear this tag at all' is itself a "
        "strong floor signal -- and run 2D-only multilateration *within* "
        "the floor that set of anchors belongs to, taking z_min/z_max from "
        "the known floor rather than from the trilateration solve.\n"
    )

    # RSSI-to-distance sanity check
    d = rssi_to_distance(rssi_dbm=-67, tx_power_dbm=-40, path_loss_exponent=2.5)
    print(f"RSSI -67dBm (TxPower -40dBm, n=2.5) -> {d:.2f} m estimated distance\n")

    # --- Multipath outlier rejection: one anchor's reading corrupted ---
    print("=== Multipath outlier rejection ===")
    print("Same layout and true position, but A3's reading is corrupted by")
    print("+3.5m (a plausible reflected-path multipath error, not just noise).\n")

    corrupted_rangings = [
        Ranging(
            anchor_id=r.anchor_id,
            distance_m=r.distance_m + (3.5 if r.anchor_id == "A3" else 0.0),
            sigma_m=r.sigma_m,
        )
        for r in rangings
    ]

    # Baseline: what the solve looks like with NO rejection at all (the
    # module's entire previous behavior), for direct comparison.
    naive_linear = _multilaterate_linear(anchors, corrupted_rangings)
    naive_position = _refine_nonlinear(naive_linear[0], anchors, corrupted_rangings)
    naive_error = math.dist(true_position, naive_position)

    corrected = multilaterate(anchors, corrupted_rangings)
    corrected_error = math.dist(true_position, corrected.position)

    print(f"Without rejection (previous behavior): error = {naive_error * 100:.1f} cm")
    print(f"With rejection:                        error = {corrected_error * 100:.1f} cm")
    print(f"Anchor(s) rejected: {corrected.rejected_anchor_ids} (expected: ['A3'])")
    print(f"Residuals (m) on the accepted solve: "
          f"{ {k: round(v, 3) for k, v in corrected.residuals_m.items()} }")

    # --- Dilution of precision: narrow-corridor geometry vs. the room ---
    print("\n=== Dilution of precision (anchor geometry, not ranging noise) ===")
    print("Same 15cm ranging noise as above, but anchors arranged along a")
    print("narrow 2m-wide corridor instead of spread across a 20x15m room.\n")

    corridor_anchors = [
        Anchor("C1", (0.0, 0.0, 2.4)),
        Anchor("C2", (0.0, 2.0, 2.4)),
        Anchor("C3", (15.0, 0.0, 2.4)),
        Anchor("C4", (15.0, 2.0, 0.3)),
    ]
    corridor_true: Point3D = (7.5, 1.0, 1.4)
    corridor_rangings = []
    for a in corridor_anchors:
        true_d = math.dist(corridor_true, a.position)
        noisy_d = true_d + rng.normal(0, 0.15)
        corridor_rangings.append(Ranging(anchor_id=a.anchor_id, distance_m=noisy_d, sigma_m=0.15))

    corridor_result = multilaterate(corridor_anchors, corridor_rangings)
    corridor_error = math.dist(corridor_true, corridor_result.position)

    print(f"Room layout:     condition number {result.condition_number:6.1f}, error {error*100:5.1f} cm")
    print(f"Corridor layout: condition number {corridor_result.condition_number:6.1f}, error {corridor_error*100:5.1f} cm")
    print(
        "\nSame 15cm ranging noise both times -- the corridor's higher "
        "condition number is exactly what predicts its larger position "
        "error before it happens, which is the point of exposing it: a "
        "caller can gate confidence (or refuse a high-risk-zone decision) "
        "on condition_number directly, not just discover after the fact "
        "that a fix in a bad-geometry area was less trustworthy."
    )
