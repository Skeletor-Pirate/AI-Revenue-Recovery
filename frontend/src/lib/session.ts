// Who is reviewing. Deliberately minimal: the dashboard is an internal
// test-mode tool, so a reviewer identifies themselves with their work email and
// that email is stamped on every ticket they take or close. There is no auth
// here and none is claimed -- the point is attribution in the audit trail, not
// access control. Real deployment would put SSO in front of this.

const KEY = 'revrec.employee_email'

/** Read the signed-in reviewer's email, or null. Safe in private/blocked storage. */
export function getEmployeeEmail(): string | null {
  try {
    return localStorage.getItem(KEY) || null
  } catch {
    return null
  }
}

export function setEmployeeEmail(email: string): void {
  try {
    localStorage.setItem(KEY, email.trim())
  } catch {
    /* storage blocked -- the session just won't persist across reloads */
  }
}

export function clearEmployeeEmail(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* no-op */
  }
}

/** Loose sanity check -- enough to catch a typo, not a validation gate. */
export function looksLikeEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}
