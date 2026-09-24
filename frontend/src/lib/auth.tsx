import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { api, authStorage } from "./api";
import type { AuthenticatedUser } from "../types/api";

interface AuthContextValue {
  authenticated: boolean;
  restoring: boolean;
  user?: AuthenticatedUser;
  signIn(values: { email: string; password: string }): Promise<AuthenticatedUser | undefined>;
  signOut(): Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: PropsWithChildren) {
  const [authenticated, setAuthenticated] = useState(() => Boolean(authStorage.getAccessToken()));
  const [user, setUser] = useState<AuthenticatedUser | undefined>();
  const [restoring, setRestoring] = useState(() => Boolean(authStorage.getAccessToken()));

  useEffect(() => {
    if (!authStorage.getAccessToken()) {
      setRestoring(false);
      return undefined;
    }

    let active = true;
    void api.auth.me()
      .then((principal) => {
        if (!active) return;
        setUser(principal);
        setAuthenticated(true);
      })
      .catch(() => {
        if (!active) return;
        authStorage.clear();
        setUser(undefined);
        setAuthenticated(false);
      })
      .finally(() => {
        if (active) setRestoring(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    authenticated,
    restoring,
    user,
    async signIn(values) {
      const authenticatedUser = await api.auth.login(values);
      setUser(authenticatedUser);
      setAuthenticated(true);
      setRestoring(false);
      return authenticatedUser;
    },
    async signOut() {
      await api.auth.logout();
      setUser(undefined);
      setAuthenticated(false);
      setRestoring(false);
    },
  }), [authenticated, restoring, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider.");
  return value;
}
