# Deploying Linear-Slack Bot on Render.com

## Prerequisites

- A Render.com account (free tier works)
- A GitHub/GitLab repo containing the `render-deploy/` files
- Your Linear API key, Slack bot token, and Slack channel ID

## Step-by-Step

### 1. Push to Git

Push the contents of this `render-deploy/` folder to the root of a GitHub or GitLab repository.

### 2. Create a Background Worker on Render

1. Go to https://dashboard.render.com
2. Click **New** > **Background Worker**
3. Connect your GitHub/GitLab repo
4. Configure:
   - **Name**: `linear-slack-bot`
   - **Runtime**: Docker
   - **Plan**: Free (or Starter for better uptime)
   - **Branch**: `main` (or your default branch)

### 3. Set Environment Variables

In the service settings, add these environment variables:

| Key | Value |
|-----|-------|
| `LINEAR_API_KEY` | Your Linear API key (lin_api_...) |
| `SLACK_BOT_TOKEN` | Your Slack bot token (xoxb-...) |
| `SLACK_CHANNEL_ID` | The Slack channel ID (e.g., C07XXXXXXXX) |

### 4. Deploy

Click **Create Background Worker**. Render will build the Docker image and start the service.

### 5. Verify

Check the **Logs** tab in Render to confirm the bot is running and polling every 3 minutes.

## Notes

- The free plan spins down after inactivity. For reliable 24/7 operation, use the **Starter** plan ($7/mo).
- State is stored in `./state.json` inside the container. It resets on each deploy, so the bot will re-check the last 24 hours after a redeploy.
- For persistent state across deploys, consider using Render's disk feature or an external store.

## Alternative: Infrastructure as Code

Instead of manual setup, you can use the included `render.yaml` file. Push it to your repo root and Render will auto-detect it via **Blueprints** (https://dashboard.render.com/blueprints).
