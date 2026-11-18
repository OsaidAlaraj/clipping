"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { CampaignDetail } from "@/components/More";
import { DetailSkeleton } from "@/components/Skeleton";

function CampaignPage() {
  const id = useSearchParams().get("id");
  return id ? <CampaignDetail id={id} /> : <div className="page"><p className="muted">No campaign selected.</p></div>;
}

export default function Page() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <CampaignPage />
    </Suspense>
  );
}
