# remove all docker containers, including the running ones
# docker rm -f $(docker ps -a -q)

# remove all unused docker images and containers
docker system prune -f

# Remove stale instance images so they rebuild with any template changes
echo "Removing old SWE-bench instance images..."
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^sweb\.eval\.' | xargs -r docker rmi -f 2>/dev/null || true

# Initialize conda for the current shell and activate environment
eval "$(conda shell.bash hook)"
conda activate agent

python hgm.py