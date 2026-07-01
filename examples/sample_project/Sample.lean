namespace Sample

/-- A tiny helper theorem used by the example final theorem. -/
theorem add_zero_once (n : Nat) : n + 0 = n := by
  exact Nat.add_zero n

/-- Adding zero twice leaves a natural number unchanged. -/
theorem add_zero_twice (n : Nat) : (n + 0) + 0 = n := by
  rw [add_zero_once]

end Sample
