/** Session secret stays in an HttpOnly cookie; CSRF token stays in memory. */
let csrf = '';
export function setCsrf(token: string | null) {csrf = token ?? '';}
export function csrfHeaders(): Record<string, string> {return csrf ? {'X-Traceglass-CSRF':csrf} : {};}
export function authenticationExpired() {window.dispatchEvent(new Event('traceglass-auth-expired'));}
export function notifyAccountChange() {
  // No identity, password or token is persisted. Other tabs discard old views.
  try {localStorage.setItem('traceglass-account-changed',crypto.randomUUID());} catch { /* Storage may be disabled. */ }
}
export async function authFetch(input: string, init: RequestInit = {}) {
  const response=await fetch(input,{...init,credentials:'same-origin',headers:{...csrfHeaders(),...init.headers}});
  if (response.status===401 && !input.includes('/auth/')) authenticationExpired();
  return response;
}
