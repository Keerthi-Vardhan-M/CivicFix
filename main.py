from collections import defaultdict
from utils.impact import query_nearby_amenities, compute_impact_score

import base64
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from utils.escalator import run_escalation_check
from utils.db import get_supabase
from utils.verifier import verify_resolution


from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import google.generativeai as genai
import os
import uuid
from datetime import datetime
from dotenv import load_dotenv

from agents.analyst import analyze_complaint, analyze_without_image
from agents.router import find_department
from agents.executor import draft_complaint, send_email, post_tweet
from utils.db import (
    check_duplicate, increment_report_count,
    save_issue, update_issue_status,
    get_all_issues, get_issue_by_id
)

load_dotenv()

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI(title="CivicPulse API", version="1.0.0")

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(run_escalation_check, "interval", hours=1)
    scheduler.start()
    print("[ESCALATOR] Scheduler started — checking every hour.")

@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"message": "CivicPulse API running", "version": "1.0.0"}


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.post("/report")
async def report_issue(
    description: str = Form(...),
    location_name: str = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    image: UploadFile = File(None)
):
    """
    Main endpoint: Receives complaint, runs all 3 agents, saves, sends.
    Returns issue ID and full result.
    """
    steps = []

    try:
        # ── STEP 1: Agent A — Analyze ──────────────────────────────
        steps.append({"step": "analyzing", "message": "Analyzing your complaint..."})

        if image:
            image_bytes = await image.read()
            analysis = await analyze_complaint(image_bytes, description, location_name)
        else:
            image_bytes = None
            analysis = await analyze_without_image(description, location_name)

       # if not analysis["success"]:
        #    raise HTTPException(status_code=400, detail=f"Analysis failed: {analysis['error']}")
        if not analysis["success"]:
             print("ANALYSIS ERROR:", analysis)
             raise HTTPException(status_code=400, detail=f"Analysis failed: {analysis['error']}")

        analysis_data = analysis["data"]

        if not analysis_data.get("is_valid_complaint", True):
            return JSONResponse(content={
                "success": False,
                "message": "This doesn't appear to be a valid civic complaint. Please provide a clear description and/or photo.",
                "steps": steps
            })

        steps.append({
            "step": "analyzed",
            "message": f"Issue identified: {analysis_data['category'].title()} (Severity {analysis_data['severity']}/10)"
        })

        # ── STEP 2: Duplicate Check ────────────────────────────────
        steps.append({"step": "checking_duplicate", "message": "Checking for existing reports nearby..."})

        duplicate_check = await check_duplicate(analysis_data["category"], lat, lng)

        if duplicate_check["is_duplicate"]:
            existing_id = duplicate_check["existing_issue_id"]
            await increment_report_count(existing_id)

            steps.append({
                "step": "duplicate_found",
                "message": f"Found existing report {existing_id[:8]}... — {duplicate_check['distance_meters']}m away. Adding your report. Total reports: {duplicate_check['existing_report_count'] + 1}"
            })

            existing_issue = await get_issue_by_id(existing_id)
            return JSONResponse(content={
                "success": True,
                "is_duplicate": True,
                "issue_id": existing_id,
                "message": f"This issue was already reported! Your report has been added ({duplicate_check['existing_report_count'] + 1} total reports). Higher count = more pressure on authorities.",
                "issue": existing_issue,
                "steps": steps
            })

        steps.append({"step": "no_duplicate", "message": "No existing reports found. Filing new complaint..."})

        # ── STEP 3: Agent B — Find Department ─────────────────────
        steps.append({"step": "routing", "message": "Finding responsible government department..."})

        dept_result = await find_department(analysis_data["category"], location_name)
        dept_data = dept_result["data"]

        steps.append({
            "step": "routed",
            "message": f"Identified: {dept_data['dept']}"
        })

        # ── STEP 4: Draft Complaint ────────────────────────────────
        steps.append({"step": "drafting", "message": "Drafting formal complaint letter..."})

        complaint = await draft_complaint(
            category=analysis_data["category"],
            severity=analysis_data["severity"],
            summary=analysis_data["summary"],
            location=location_name,
            department=dept_data["dept"]
        )

        steps.append({"step": "drafted", "message": "Complaint letter ready."})
        
        # ── STEP 4b: Community Impact Scoring ─────────────────────
        steps.append({"step": "impact_scoring", "message": "Analyzing community impact..."})

        amenities = await query_nearby_amenities(lat, lng)
        impact = compute_impact_score(amenities, analysis_data["severity"])

        steps.append({
            "step": "impact_scored",
            "message": f"{impact['impact_label']} — score {impact['impact_score']}/100"
        })




        # ── STEP 5: Save to DB ─────────────────────────────────────
        issue_id = str(uuid.uuid4())
        issue_record = {
            "id": issue_id,
            "location_name": location_name,
            "location_lat": lat,
            "location_lng": lng,
            "description": description,
            "category": analysis_data["category"],
            "severity": analysis_data["severity"],
            "severity_reason": analysis_data.get("severity_reason", ""),
            "summary": analysis_data["summary"],
            "affected_population": analysis_data.get("affected_population", "medium"),
            "urgency": analysis_data.get("urgency", "within_week"),
            "department": dept_data["dept"],
            "dept_email": dept_data.get("email", ""),
            "dept_twitter": dept_data.get("twitter", ""),
            "dept_phone": dept_data.get("phone", ""),
            "complaint_subject": complaint["subject"],
            "complaint_body": complaint["body"],
            "status": "submitted",
            "report_count": 1,
            "email_sent": False,
            "tweet_url": None,
            "created_at": datetime.now().isoformat(),
            "complaint_image_b64": base64.b64encode(image_bytes).decode() if image_bytes else None,
            "impact_score": impact["impact_score"],
            "impact_tier": impact["impact_tier"],
            "impact_label": impact["impact_label"],
            "nearest_school_m": impact["nearest_school_m"],
            "nearest_hospital_m": impact["nearest_hospital_m"],
            "bus_stops_count": impact["bus_stops_count"],


        }

        save_result = await save_issue(issue_record)
        if save_result["success"]:
            issue_record = save_result["data"]

        # ── STEP 6: Agent C — Send Email ───────────────────────────
        email_result = {"success": False, "error": "No email configured"}

        if dept_data.get("email") and os.getenv("GMAIL_REFRESH_TOKEN"):
            steps.append({"step": "sending_email", "message": f"Sending complaint to {dept_data['email']}..."})

            email_result = await send_email(
                to_email=dept_data["email"],
                subject=complaint["subject"],
                body=complaint["body"]
            )

            if email_result["success"]:
                steps.append({"step": "email_sent", "message": f"Email sent to {dept_data['email']}"})
                await update_issue_status(issue_id, "submitted", {"email_sent": True})
            else:
                steps.append({"step": "email_failed", "message": "Email sending failed (API issue)"})
        else:
            steps.append({"step": "email_skipped", "message": "Email not configured (demo mode)"})

        # ── STEP 7: Agent C — Post Tweet ───────────────────────────
        tweet_result = {"success": False}

        if os.getenv("TWITTER_API_KEY"):
            steps.append({"step": "tweeting", "message": "Posting public accountability tweet..."})

            tweet_result = await post_tweet(
                category=analysis_data["category"],
                severity=analysis_data["severity"],
                summary=analysis_data["summary"],
                location=location_name,
                dept_twitter=dept_data.get("twitter", "@BBMP_PALIKE")
            )

            if tweet_result["success"]:
                steps.append({"step": "tweeted", "message": f"Tweet posted: {tweet_result.get('tweet_url')}"})
                await update_issue_status(issue_id, "submitted", {"tweet_url": tweet_result.get("tweet_url")})
            else:
                steps.append({"step": "tweet_failed", "message": "Tweet posting failed (API issue)"})
        else:
            steps.append({"step": "tweet_skipped", "message": "Twitter not configured (demo mode)"})

        steps.append({"step": "complete", "message": "Complaint filed successfully!"})

        return JSONResponse(content={
            "success": True,
            "is_duplicate": False,
            "issue_id": issue_id,
            "message": "Complaint filed and sent successfully!",
            "analysis": analysis_data,
            "department": dept_data,
            "complaint": complaint,
            "email_sent": email_result.get("success", False),
            "tweet_url": tweet_result.get("tweet_url"),
            "tweet_text": tweet_result.get("tweet_text"),
            "steps": steps
        })

    except HTTPException:
        raise
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e), "steps": steps}
        )


@app.get("/issue/{issue_id}")
async def get_issue(issue_id: str):
    """Get issue details by ID."""
    issue = await get_issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    return issue


@app.get("/issues")
async def list_issues():
    """Get all issues for map display."""
    issues = await get_all_issues()
    return {"issues": issues, "total": len(issues)}


@app.patch("/issue/{issue_id}/status")
async def update_status(issue_id: str, status: str):
    """Manually update issue status."""
    valid_statuses = ["submitted", "acknowledged", "in_progress", "resolved", "rejected"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {valid_statuses}")

    result = await update_issue_status(issue_id, status)
    return result

@app.post("/issue/{issue_id}/verify-resolution")
async def verify_resolution_endpoint(
    issue_id: str,
    after_image: UploadFile = File(...)
):
    """
    Government employee submits after-photo.
    AI compares with original complaint, validates resolution.
    """
    # Fetch issue from DB
    issue = await get_issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    if issue["status"] == "resolved":
        raise HTTPException(status_code=400, detail="Issue already marked resolved.")

    after_bytes = await after_image.read()

    # Check if original complaint had an image stored
    before_b64 = issue.get("complaint_image_b64")

    if before_b64:
        before_bytes = base64.b64decode(before_b64)
        result = await verify_resolution(before_bytes, after_bytes)
    else:
        # No before image — auto-accept but flag it
        result = {
            "success": True,
            "data": {
                "is_resolved": True,
                "resolution_score": 50,
                "verdict": "partially_resolved",
                "notes": "No original complaint image available for comparison. Marked resolved based on government submission only.",
                "before_description": "Not available",
                "after_description": "Submitted by government employee."
            }
        }

    if not result["success"]:
        raise HTTPException(status_code=500, detail=f"Verification failed: {result['error']}")

    verification = result["data"]

    # Determine new status based on verdict
    verdict = verification.get("verdict")
    new_status = "resolved" if verdict == "fully_resolved" else "in_progress"

    # Save after image + verification result to DB
    supabase = get_supabase()  # import this at top if not already
    after_b64 = base64.b64encode(after_bytes).decode()

    supabase.table("issues").update({
        "resolved_image_b64": after_b64,
        "resolution_verified": verification["is_resolved"],
        "resolution_score": verification["resolution_score"],
        "resolution_notes": verification["notes"],
        "resolved_at": datetime.now(timezone.utc).isoformat() if verdict == "fully_resolved" else None,
        "status": new_status
    }).eq("id", issue_id).execute()

    return {
        "success": True,
        "issue_id": issue_id,
        "verdict": verdict,
        "resolution_score": verification["resolution_score"],
        "is_resolved": verification["is_resolved"],
        "notes": verification["notes"],
        "before_description": verification.get("before_description"),
        "after_description": verification.get("after_description"),
        "new_status": new_status
    }
@app.get("/heatmap")
async def get_heatmap():
    """
    Returns clustered heatmap data for frontend map rendering.
    Groups nearby issues into hotspots with aggregate stats.
    """
    issues = await get_all_issues()

    if not issues:
        return {"clusters": [], "hotspots": [], "total_issues": 0}

    # ── Step 1: Build heatmap points (one per issue, weighted by severity + report_count) ──
    heatmap_points = []
    for issue in issues:
        if not issue.get("location_lat") or not issue.get("location_lng"):
            continue

        weight = (issue.get("severity", 5) / 10) * (1 + (issue.get("report_count", 1) - 1) * 0.3)
        weight = round(min(weight, 3.0), 2)

        heatmap_points.append({
            "lat": issue["location_lat"],
            "lng": issue["location_lng"],
            "weight": weight,
            "issue_id": issue["id"],
            "category": issue["category"],
            "severity": issue["severity"],
            "status": issue["status"]
        })

    # ── Step 2: Cluster nearby issues (within ~400m grid cells) ───
    GRID_SIZE = 0.004  # ~400m in degrees

    grid = defaultdict(list)
    for point in heatmap_points:
        cell_lat = round(point["lat"] / GRID_SIZE) * GRID_SIZE
        cell_lng = round(point["lng"] / GRID_SIZE) * GRID_SIZE
        grid[(round(cell_lat, 6), round(cell_lng, 6))].append(point)

    clusters = []
    for (cell_lat, cell_lng), points in grid.items():
        avg_lat = sum(p["lat"] for p in points) / len(points)
        avg_lng = sum(p["lng"] for p in points) / len(points)
        avg_severity = round(sum(p["severity"] for p in points) / len(points), 1)
        total_weight = round(sum(p["weight"] for p in points), 2)

        category_counts = defaultdict(int)
        status_counts = defaultdict(int)
        for p in points:
            category_counts[p["category"]] += 1
            status_counts[p["status"]] += 1

        dominant_category = max(category_counts, key=category_counts.get)

        clusters.append({
            "lat": round(avg_lat, 6),
            "lng": round(avg_lng, 6),
            "issue_count": len(points),
            "total_weight": total_weight,
            "avg_severity": avg_severity,
            "dominant_category": dominant_category,
            "category_breakdown": dict(category_counts),
            "status_breakdown": dict(status_counts),
            "issue_ids": [p["issue_id"] for p in points]
        })

    clusters.sort(key=lambda x: x["total_weight"], reverse=True)

    # ── Step 3: Tag top clusters as hotspots ──────────────────────
    hotspots = []
    for cluster in clusters:
        if cluster["issue_count"] >= 2 or cluster["avg_severity"] >= 7:
            hotspots.append({
                **cluster,
                "hotspot_level": (
                    "critical" if cluster["total_weight"] >= 4 else
                    "high"     if cluster["total_weight"] >= 2 else
                    "moderate"
                )
            })

    # ── Step 4: Category summary ──────────────────────────────────
    all_categories = defaultdict(int)
    all_statuses = defaultdict(int)
    for issue in issues:
        all_categories[issue["category"]] += 1
        all_statuses[issue["status"]] += 1

    return {
        "heatmap_points": heatmap_points,
        "clusters": clusters,
        "hotspots": hotspots,
        "summary": {
            "total_issues": len(issues),
            "total_clusters": len(clusters),
            "total_hotspots": len(hotspots),
            "category_breakdown": dict(all_categories),
            "status_breakdown": dict(all_statuses),
            "avg_severity": round(
                sum(i.get("severity", 0) for i in issues) / len(issues), 1
            ) if issues else 0
        }
    }




