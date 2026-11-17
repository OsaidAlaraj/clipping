"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import Feed from "@/components/Feed";

function ReviewPage() {
  const campaign = useSearchParams().get("campaign") ?? undefined;
  return <Feed key={campaign ?? "all"} campaignId={campaign} />;
}

export default function Page() {
  return (
    <Suspense fallback={null}>
      <ReviewPage />
    </Suspense>
  );
}
