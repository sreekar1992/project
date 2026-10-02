import { useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
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
  const queryClient = useQueryClient();
  const actionGeneration = useRef(0);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState<AuthenticatedUser | undefined>();
  const [restoring, setRestoring] = useState(true);

  useEffect(() => {
    let active = true;
    const generationAtStart = actionGeneration.current;
    void api.auth.restore()
      .then((principal) => {
        if (!active || generationAtStart !== actionGeneration.current) return;
        if (principal) {
          setUser(principal);
          setAuthenticated(true);
        } else {
          setUser(undefined);
          setAuthenticated(false);
        }
      })
      .catch(() => {
        if (!active || generationAtStart !== actionGeneration.current) return;
        authStorage.clear();
        setUser(undefined);
        setAuthenticated(false);
      })
      .finally(() => {
        if (active && generationAtStart === actionGeneration.current) setRestoring(false);
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
      actionGeneration.current += 1;
      setUser(authenticatedUser);
      setAuthenticated(true);
      setRestoring(false);
      return authenticatedUser;
    },
    async signOut() {
      actionGeneration.current += 1;
      authStorage.markSignedOut();
      try {
        await api.auth.logout();
      } catch {
        // Local sign-out must still complete when the server is temporarily
        // unavailable. The explicit sign-out marker prevents a stale cookie
        // from restoring this browser session on the next page load.
      } finally {
        authStorage.clear();
        queryClient.clear();
        setUser(undefined);
        setAuthenticated(false);
        setRestoring(false);
      }
    },
  }), [authenticated, queryClient, restoring, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider.");
  return value;
}
