"""当前单响应概览。"""
def analyze(response):
    lines=[line for line in response.body.decode('utf-8').splitlines() if line.strip()]
    return {'nonempty_lines':len(lines),'content_type':response.header('content-type')}
