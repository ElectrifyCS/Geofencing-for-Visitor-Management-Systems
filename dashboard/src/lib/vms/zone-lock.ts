/**
 * Port of tracking.py ZoneLockTracker — consecutive-read debounce so a tag
 * hovering on a boundary cannot fire an endless zone_entry/zone_exit stream.
 */

export class ZoneLockTracker {
  lockedZone: string | null = null;
  private candidate: string | null = null;
  private count = 0;

  minConfirmReadings: number;

  constructor(minConfirmReadings = 3) {
    this.minConfirmReadings = minConfirmReadings;
  }

  reset(): void {
    this.lockedZone = null;
    this.candidate = null;
    this.count = 0;
  }

  update(rawZone: string | null): string | null {
    if (rawZone === this.lockedZone) {
      this.candidate = null;
      this.count = 0;
      return this.lockedZone;
    }
    if (rawZone === this.candidate) this.count += 1;
    else {
      this.candidate = rawZone;
      this.count = 1;
    }
    if (this.count >= this.minConfirmReadings) {
      this.lockedZone = this.candidate;
      this.candidate = null;
      this.count = 0;
    }
    return this.lockedZone;
  }
}
