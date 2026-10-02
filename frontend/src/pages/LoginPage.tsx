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
    <Box
      sx={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        position: "relative",
        overflow: "hidden",
        py: { xs: 2, sm: 4 },
        backgroundColor: "#075d86",
        backgroundImage: "linear-gradient(122deg, rgba(3, 47, 83, 0.92), rgba(0, 137, 163, 0.62) 48%, rgba(15, 65, 176, 0.79)), url('/assets/ecg-hero-background.png')",
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      <Box aria-hidden sx={{ position: "absolute", inset: 0, background: "radial-gradient(circle at 15% 88%, rgba(47, 226, 217, 0.35), transparent 24rem)" }} />
      <Container maxWidth="lg" sx={{ position: "relative", zIndex: 1 }}>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "minmax(0, 1fr)", md: "minmax(0, 1.1fr) minmax(380px, 0.9fr)" }, gap: { xs: 3, md: 7 }, alignItems: "center" }}>
          <Stack spacing={3} sx={{ color: "common.white", display: { xs: "none", md: "flex" }, py: 4 }}>
            <Stack direction="row" spacing={1.4} alignItems="center">
              <Box sx={{ display: "grid", placeItems: "center", width: 52, height: 52, borderRadius: 3, bgcolor: "rgba(255,255,255,0.17)", border: "1px solid rgba(255,255,255,0.34)", backdropFilter: "blur(8px)" }}>
                <MonitorHeartOutlinedIcon fontSize="large" />
              </Box>
              <Box>
                <Typography variant="overline" sx={{ color: "#bff9f5" }}>MITS 26MPE05015</Typography>
                <Typography variant="h5">ECG Research Clinical Platform</Typography>
              </Box>
            </Stack>
            <Box>
              <Typography variant="h3" sx={{ maxWidth: 570 }}>A clearer, safer workspace for ECG research.</Typography>
              <Typography sx={{ mt: 2, maxWidth: 560, color: "rgba(242, 253, 255, 0.88)", fontSize: "1.08rem", lineHeight: 1.65 }}>
                Securely access authorized hospital workflows, waveform review, and clinician-governed research results in one focused environment.
              </Typography>
            </Box>
            <Paper elevation={0} sx={{ maxWidth: 570, p: 2.25, color: "common.white", bgcolor: "rgba(2, 38, 72, 0.34)", borderColor: "rgba(210, 253, 255, 0.25)", boxShadow: "none", backdropFilter: "blur(10px)" }}>
              <Typography variant="overline" sx={{ color: "#7ff5eb" }}>Qualified review required</Typography>
              <Typography variant="body2" sx={{ color: "rgba(242, 253, 255, 0.9)", lineHeight: 1.6 }}>
                AI output supports research and clinical review only. It is never an autonomous diagnosis or treatment recommendation.
              </Typography>
            </Paper>
          </Stack>

          <Stack spacing={2}>
            <Stack direction="row" spacing={1.2} alignItems="center" justifyContent="center" sx={{ display: { xs: "flex", md: "none" }, color: "common.white" }}>
              <MonitorHeartOutlinedIcon fontSize="large" />
              <Typography variant="h5">ECG Research Clinical Platform</Typography>
            </Stack>
            <ClinicalSafetyBanner />
            <Paper
              component="form"
              onSubmit={handleSubmit((values) => login.mutate(values))}
              sx={{
                p: { xs: 3, sm: 4 },
                backgroundColor: "rgba(255, 255, 255, 0.96)",
                backgroundImage: "linear-gradient(120deg, rgba(255,255,255,0.97) 40%, rgba(255,255,255,0.78)), url('/assets/dna-watermark.png')",
                backgroundPosition: "center, right bottom",
                backgroundRepeat: "no-repeat",
                backgroundSize: "auto, 300px auto",
                boxShadow: "0 24px 64px rgba(0, 34, 68, 0.27)",
              }}
            >
            <Stack spacing={2.25}>
              <Box>
                <Typography variant="overline" color="primary.main">Authorized access</Typography>
                <Typography variant="h4">Welcome back</Typography>
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
        </Box>
      </Container>
    </Box>
  );
}
