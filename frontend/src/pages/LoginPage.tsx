import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import MonitorHeartOutlinedIcon from "@mui/icons-material/MonitorHeartOutlined";
import { zodResolver } from "@hookform/resolvers/zod";
import { Alert, Box, Button, Container, Paper, Stack, TextField, Typography } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { useAuth } from "../lib/auth";
import { ClinicalSafetyBanner } from "../components/ClinicalSafetyBanner";
import { apiErrorMessage } from "../lib/api";
import { LoadingState } from "../components/LoadingState";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

type LoginValues = z.infer<typeof loginSchema>;

export function LoginPage() {
  const { authenticated, restoring, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const returnTo = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? "/";
  const { register, handleSubmit, formState: { errors } } = useForm<LoginValues>({ resolver: zodResolver(loginSchema) });
  const login = useMutation({
    mutationFn: signIn,
    onSuccess: (authenticatedUser) => {
      const destination = returnTo === "/" && authenticatedUser?.roles?.includes("SUPER_ADMIN")
        ? "/super-admin"
        : returnTo;
      navigate(destination, { replace: true });
    },
  });

  if (restoring) return <LoadingState label="Restoring your authorized workspace…" />;
  if (authenticated) return <Navigate to="/" replace />;

  return (
    <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", bgcolor: "background.default", py: 4 }}>
      <Container maxWidth="sm">
        <Stack spacing={2}>
          <Stack direction="row" spacing={1.2} alignItems="center" justifyContent="center">
            <MonitorHeartOutlinedIcon color="primary" fontSize="large" />
            <Typography variant="h5">ECG Research Clinical Platform</Typography>
          </Stack>
          <ClinicalSafetyBanner />
          <Paper component="form" onSubmit={handleSubmit((values) => login.mutate(values))} sx={{ p: { xs: 3, sm: 4 } }}>
            <Stack spacing={2.25}>
              <Box>
                <Typography variant="h5">Sign in</Typography>
                <Typography color="text.secondary" sx={{ mt: 0.5 }}>
                  Use your hospital-managed account. Server-side roles and tenant isolation determine access.
                </Typography>
              </Box>
              {login.isError && <Alert severity="error">{apiErrorMessage(login.error)}</Alert>}
              <TextField
                autoComplete="email"
                label="Email"
                type="email"
                fullWidth
                error={Boolean(errors.email)}
                helperText={errors.email?.message}
                {...register("email")}
              />
              <TextField
                autoComplete="current-password"
                label="Password"
                type="password"
                fullWidth
                error={Boolean(errors.password)}
                helperText={errors.password?.message}
                {...register("password")}
              />
              <Button type="submit" variant="contained" size="large" startIcon={<LockOutlinedIcon />} disabled={login.isPending}>
                {login.isPending ? "Signing in…" : "Sign in securely"}
              </Button>
              <Typography variant="caption" color="text.secondary">
                This interface is for research and qualified clinician review. Do not use AI results as an autonomous clinical decision.
              </Typography>
            </Stack>
          </Paper>
        </Stack>
      </Container>
    </Box>
  );
}
