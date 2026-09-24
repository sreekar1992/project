import { Box, CircularProgress, Typography } from "@mui/material";

export function LoadingState({ label = "Loading securely…" }: { label?: string }) {
  return (
    <Box sx={{ minHeight: 180, display: "grid", placeItems: "center", gap: 1 }}>
      <CircularProgress size={30} />
      <Typography color="text.secondary" variant="body2">{label}</Typography>
    </Box>
  );
}
