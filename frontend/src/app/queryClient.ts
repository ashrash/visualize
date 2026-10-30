import { QueryClient } from "@tanstack/react-query";

/** One client for the app; non-React code (job settlement) invalidates queries through it. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});
