import { Button, Paper, Stack, Typography } from "@mui/material";
import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <Paper sx={{ p: 4 }}>
      <Stack spacing={2} alignItems="flex-start">
        <Typography variant="h4">Page not found</Typography>
        <Typography color="text.secondary">This route is not available in the current research workspace.</Typography>
        <Button component={Link} to="/" variant="contained">Return to dashboard</Button>
      </Stack>
    </Paper>
  );
}
