import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ErrorBoundary } from "react-error-boundary";

import { App } from "@/App";
import { RootErrorFallback } from "@/RootErrorFallback";
import { logBoot } from "@/lib/log";
import "@/index.css";

// One structured breadcrumb per app load — the frontend observability seam (see lib/log.ts).
logBoot();

const queryClient = new QueryClient({
    defaultOptions: {
        queries: { staleTime: 1_000, refetchOnWindowFocus: false },
    },
});

// Code-based route tree (one blank route). The file-based router + codegen
// (routeTree.gen.ts) lands with the real pages in the MVP-2 UI spec.
const rootRoute = createRootRoute({ component: () => <Outlet /> });
const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: App });
const routeTree = rootRoute.addChildren([indexRoute]);
const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
    interface Register {
        router: typeof router;
    }
}

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing");

createRoot(rootEl).render(
    <StrictMode>
        <ErrorBoundary FallbackComponent={RootErrorFallback}>
            <QueryClientProvider client={queryClient}>
                <RouterProvider router={router} />
            </QueryClientProvider>
        </ErrorBoundary>
    </StrictMode>,
);
