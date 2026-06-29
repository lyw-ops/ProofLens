namespace Sample

/-- A tiny helper lemma used by the example final theorem. -/
lemma add_zero_once (n : Nat) : n + 0 = n := by
  exact Nat.add_zero n

/-- Adding zero twice leaves a natural number unchanged. -/
theorem add_zero_twice (n : Nat) : (n + 0) + 0 = n := by
  rw [add_zero_once]
  exact add_zero_once n

end Sample

