"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import Feed from "@/components/Feed";
import { FeedSkeleton } from "@/components/Skeleton";

function ReviewPage() {
  const campaign = useSearchParams().get("campaign") ?? undefined;
  const router = useRouter();
  return <Feed key={campaign ?? "all"} campaignId={campaign} onExit={() => router.push("/")} />;
}

export default function Page() {
  return (
    <Suspense fallback={<FeedSkeleton />}>
      <ReviewPage />
    </Suspense>
  );
}
