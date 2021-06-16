import requests
import datetime
import json
import os
import re
import statistics
import math

api_url = 'https://api.github.com/'
cache_path = '~/.cache'
oauth_file = '~/.secrets/github-reports'
oauth_token = ''

verbose = True

timeframes = [
    {
        'part': {
            'label': 'Q3',
            'start': '8/1/2020',
            'end':   '10/31/2020',
        },
        'whole': {
            'label': 'FY21',
            'start': '2/1/2020',
            'end':   '10/31/2020',
        }
    },
    {
        'part': {
            'label': 'Q4',
            'start': '11/1/2020',
            'end':   '1/31/2021',
        },
        'whole': {
            'label': 'FY21',
            'start': '2/1/2020',
            'end':   '1/31/2021',
        }
    },
    {
        'part': {
            'label': 'Q1',
            'start': '2/1/2021',
            'end':   '4/30/2021',
        },
        'whole': {
            'label': 'FY22',
            'start': '2/1/2021',
            'end':   '1/31/2022',
        }
    },
]

projects = [
    'pivotal/kpack',
    'concourse',
    'paketo-buildpacks',
    'buildpacks',
]

our_orgs = [
    'pivotal',
    'pivotal-legacy',
    'vmware',
    'vmware-tanzu'
]

def to_date(source):
    if source is None:
        return None
    if isinstance(source, str):
        if re.match('[1-2]?[0-9]/[1-3]?[0-9]/[0-9]+', source) is not None:
            return datetime.datetime.strptime(source, "%m/%d/%Y")
        if re.match('[0-9]+-[0-9]+-[0-9]+T[0-9]+:[0-9]+:[0-9]+Z', source) is not None:
            return datetime.datetime.strptime(source, '%Y-%m-%dT%H:%M:%SZ')
        print('------- date? ', source)
        return None
    return source

def in_range(date, daterange):
    if daterange is None:
        return True
    date = to_date(date)
    start = daterange.get('start')
    if start is not None and date < start:
        return False
    end = daterange.get('end')
    if end is not None and date > end:
        return False
    return True

def overlaps_range(left, right, daterange):
    if daterange is None:
        return True
    if left is not None:
        left = to_date(left)
        end = daterange.get('end')
        if end is not None and left > end:
            return False
    if right is not None:
        right = to_date(right)
        start = daterange.get('start')
        if start is not None and right < start:
            return False
    return True

def range(start, end):
    result = {}
    if start is not None:
        result['start'] = to_date(start)
    if end is not None:
        result['end'] = to_date(end)
    return result

def read_token():
    global oauth_token
    with open(os.path.expanduser(oauth_file)) as f:
        oauth_token = f.read().replace('\n', '')

def get_links(response):
    link_header = response.headers.get('Link')
    if link_header is None:
        return {}
    link_values = link_header.split(', ')
    links = {}
    for link in link_values:
        parts = link.split('; rel=')
        url = parts[0].strip('<>')
        rel = parts[1].strip('"')
        links[rel] = url
    return links

def append(collection, items):
    if collection is None:
        return items
    if items is None:
        return collection
    if isinstance(collection, list):
        if isinstance(items, list):
            collection.extend(items)
            return collection
        else:
            collection.append(items)
            return collection
    else:
        if isinstance(items, list):
            items.insert(0, collection)
            return items
        else:
            return [ collection, items ]

def get_paged_results(url):
    results = None
    print(url, '', end='', flush=True)
    url = api_url + url
    headers = {
        'Authorization': 'token {}'.format(oauth_token)
    }
    params = {
        'per_page': 100
    }
    while url is not None:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        links = get_links(response)
        results = append(results, response.json())
        url = links.get('next')
        if url is not None:
            print('.', end='', flush=True)
    print()
    return results

def get_cached_results(url):
    cache_dir = os.path.expanduser(cache_path)
    rel_url = url.removeprefix(api_url)
    cache_filename = cache_dir + '/' + rel_url.replace('/', '_')
    if os.path.exists(cache_filename):
        with open(cache_filename) as json_file:
            data = json.load(json_file)
        return data
    else:
        data = get_paged_results(rel_url)
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        with open(cache_filename, 'w') as json_file:
            json.dump(data, json_file, indent=3)
        return data

def get_repo_commits(owner, repo):
    url = 'repos/{owner}/{repo}/commits'.format(owner=owner, repo=repo)
    commits = get_cached_results(url)
    return commits

def filter_commits(commits, daterange):
    if daterange is None:
        return commits
    filtered_commits = []
    for commit in commits:
        author = commit.get('author')
        if author is None:
            continue
        if author.get('type') != 'User':
            continue
        date = commit.get('commit', {}).get('author', {}).get('date')
        if in_range(date, daterange):
            filtered_commits.append(commit)
    return filtered_commits

def get_repo_pulls(owner, repo):
    url = 'repos/{owner}/{repo}/pulls?state=all'.format(owner=owner, repo=repo)
    pulls = get_cached_results(url)
    return pulls

def get_org_pulls(owner, repos):
    pulls = []
    for repo in repos:
        pulls.extend(get_repo_pulls(owner, repo.get('name')))
    return pulls

def filter_pulls(pulls, daterange):
    if daterange is None:
        return pulls
    filtered_pulls = []
    for pull in pulls:
        created_at = pull.get('created_at')
        closed_at = pull.get('closed_at')
        if overlaps_range(created_at, closed_at, daterange):
            filtered_pulls.append(pull)
    return filtered_pulls

def compute_median_review_duration(pulls, daterange = None):
    durations=[]
    for pull in pulls:
        created_at = to_date(pull.get('created_at'))
        closed_at = to_date(pull.get('closed_at', pull.get('merged_at')))
        if closed_at is None and daterange is not None:
            closed_at = daterange.get('end')
        else:
            closed_at = datetime.datetime.now()
        duration = closed_at - created_at
        durations.append(duration.total_seconds())
        number = pull.get('number')
        # print('   pull request {number}: created {created}, closed {closed}, duration {duration}'.format(
        #     number=number,
        #     created=created_at.strftime('%d/%m/%Y'),
        #     closed=closed_at.strftime('%d/%m/%Y'),
        #     duration=math.floor(duration.total_seconds() / (60 * 60 * 24))
        # ))
    median = math.floor(statistics.median(durations) / (60 * 60 * 24))
    return median

def to_date(source):
    if source is None:
        return None
    if isinstance(source, str):
        if re.match('[1-2]?[0-9]/[1-3]?[0-9]/[0-9]+', source) is not None:
            return datetime.datetime.strptime(source, "%m/%d/%Y")
        if re.match('[0-9]+-[0-9]+-[0-9]+T[0-9]+:[0-9]+:[0-9]+Z', source) is not None:
            return datetime.datetime.strptime(source, '%Y-%m-%dT%H:%M:%SZ')
        print('------- date? ', source)
        return None
    return source

def in_range(date, daterange):
    if daterange is None:
        return True
    date = to_date(date)
    start = daterange.get('start')
    if start is not None and date < start:
        return False
    end = daterange.get('end')
    if end is not None and date > end:
        return False
    return True

def overlaps_range(left, right, daterange):
    if daterange is None:
        return True
    if left is not None:
        left = to_date(left)
        end = daterange.get('end')
        if end is not None and left > end:
            return False
    if right is not None:
        right = to_date(right)
        start = daterange.get('start')
        if start is not None and right < start:
            return False
    return True

def range(start, end):
    result = {}
    if start is not None:
        result['start'] = to_date(start)
    if end is not None:
        result['end'] = to_date(end)
    return result

def read_token():
    global oauth_token
    with open(os.path.expanduser(oauth_file)) as f:
        oauth_token = f.read().replace('\n', '')

def get_links(response):
    link_header = response.headers.get('Link')
    if link_header is None:
        return {}
    link_values = link_header.split(', ')
    links = {}
    for link in link_values:
        parts = link.split('; rel=')
        url = parts[0].strip('<>')
        rel = parts[1].strip('"')
        links[rel] = url
    return links

def append(collection, items):
    if collection is None:
        return items
    if items is None:
        return collection
    if isinstance(collection, list):
        if isinstance(items, list):
            collection.extend(items)
            return collection
        else:
            collection.append(items)
            return collection
    else:
        if isinstance(items, list):
            items.insert(0, collection)
            return items
        else:
            return [ collection, items ]

def get_paged_results(url):
    results = None
    print(url, '', end='', flush=True)
    url = api_url + url
    headers = {
        'Authorization': 'token {}'.format(oauth_token)
    }
    params = {
        'per_page': 100
    }
    while url is not None:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        links = get_links(response)
        results = append(results, response.json())
        url = links.get('next')
        if url is not None:
            print('.', end='', flush=True)
    print()
    return results

def get_cached_results(url):
    cache_dir = os.path.expanduser(cache_path)
    rel_url = url.removeprefix(api_url)
    cache_filename = cache_dir + '/' + rel_url.replace('/', '_')
    if os.path.exists(cache_filename):
        with open(cache_filename) as json_file:
            data = json.load(json_file)
        return data
    else:
        data = get_paged_results(rel_url)
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        with open(cache_filename, 'w') as json_file:
            json.dump(data, json_file, indent=3)
        return data

def get_repo_commits(owner, repo):
    url = 'repos/{owner}/{repo}/commits'.format(owner=owner, repo=repo)
    commits = get_cached_results(url)
    return commits

def filter_commits(commits, daterange):
    if daterange is None:
        return commits
    filtered_commits = []
    for commit in commits:
        author = commit.get('author')
        if author is None:
            continue
        if author.get('type') != 'User':
            continue
        date = commit.get('commit', {}).get('author', {}).get('date')
        if in_range(date, daterange):
            filtered_commits.append(commit)
    return filtered_commits

def get_repo_pulls(owner, repo):
    url = 'repos/{owner}/{repo}/pulls?state=all'.format(owner=owner, repo=repo)
    pulls = get_cached_results(url)
    return pulls

def get_org_pulls(owner, repos):
    pulls = []
    for repo in repos:
        pulls.extend(get_repo_pulls(owner, repo.get('name')))
    return pulls

def filter_pulls(pulls, daterange):
    if daterange is None:
        return pulls
    filtered_pulls = []
    for pull in pulls:
        created_at = pull.get('created_at')
        closed_at = pull.get('closed_at')
        if overlaps_range(created_at, closed_at, daterange):
            filtered_pulls.append(pull)
    return filtered_pulls

def compute_median_review_duration(pulls, daterange = None):
    durations=[]
    for pull in pulls:
        created_at = to_date(pull.get('created_at'))
        closed_at = to_date(pull.get('closed_at', pull.get('merged_at')))
        if closed_at is None and daterange is not None:
            closed_at = daterange.get('end')
        else:
            closed_at = datetime.datetime.now()
        duration = closed_at - created_at
        durations.append(duration.total_seconds())
        number = pull.get('number')
        # print('   pull request {number}: created {created}, closed {closed}, duration {duration}'.format(
        #     number=number,
        #     created=created_at.strftime('%d/%m/%Y'),
        #     closed=closed_at.strftime('%d/%m/%Y'),
        #     duration=math.floor(duration.total_seconds() / (60 * 60 * 24))
        # ))
    if len(durations) < 1:
        return 0.0
    median = math.floor(statistics.median(durations) / (60 * 60 * 24))
    return median

def get_pull_comments(pull):
    url = pull.get('_links', {}).get('comments', {}).get('href')
    if url is not None:
        comments = get_cached_results(url)
        return comments
    return []

def first_non_author_comment(pull):
    timestamp = to_date(pull.get('closed_at'))
    if timestamp is None:
        timestamp = datetime.datetime.now()
    comments = get_pull_comments(pull)
    author = pull.get('user', {}).get('login', 'anonymous')
    for comment in comments:
        commenter = comment.get('user', {}).get('login', 'anonymous')
        if commenter == author:
            continue
        if comment.get('user', {}).get('type') != 'User':
            continue
        commented_at = to_date(comment.get('created_at'))
        if commented_at < timestamp:
            timestamp = commented_at
    return timestamp

def compute_median_response_time(pulls):
    durations=[]
    for pull in pulls:
        created_at = to_date(pull.get('created_at'))
        comment_at = first_non_author_comment(pull)
        response_time = comment_at - created_at
        durations.append(response_time.total_seconds())
    if len(durations) < 1:
        return 0.0
    median = math.floor(statistics.median(durations))
    return median

def get_repo_contributors(owner, repo, daterange = None):
    commits = filter_commits(get_repo_commits(owner, repo), daterange)
    contributors = {}
    for commit in commits:
        author = commit.get('author')
        if author is None:
            continue
        if author.get('type') != 'User':
            continue
        login = author.get('login', 'anonymous')
        contributors[login] = {
            'login': login
        }
    return contributors

def get_org_contributors(owner, repos, daterange = None):
    contributors = {}
    for repo in repos:
        contributors.update(get_repo_contributors(owner, repo.get('name'), daterange))
    return contributors

our_members = {}

def get_our_members():
    # this is relatively expensive so only do this once
    global our_members
    if len(our_members) < 1:
        for org in our_orgs:
            members = get_org_members(org)
            for member in members:
                login = member.get('login', 'anonymous')
                our_members[login] = True
    return our_members
    
def is_ours(login):
    return login in get_our_members()

def get_our_pulls(pulls):
    our_pulls = []
    for pull in pulls:
        login = pull.get('user', {}).get('login', 'anonymous')
        if is_ours(login):
            our_pulls.append(pull)
    return our_pulls

def get_org_members(org):
    members = get_cached_results('orgs/{org}/members'.format(org=org))
    return members

def friendly_duration(seconds):
    if seconds < 90:
        return '{seconds} seconds'.format(seconds=seconds)
    minutes = math.floor(seconds / 60)
    if minutes < 90:
        return '{minutes} minutes'.format(minutes=minutes)
    hours = math.floor(minutes / 60)
    if hours < 24:
        return '{hours} hours'.format(hours=hours)
    days = math.floor(hours / 24)
    return '{days} days'.format(days=days)

def report_single_repo(owner, repo, timeframe):

    whole = range(timeframe['whole']['start'], timeframe['whole']['end'])
    part = range(timeframe['part']['start'], timeframe['part']['end'])
    wname = timeframe['whole']['label']
    pname = timeframe['part']['label']

    print('\n{owner}/{repo}'.format(owner=owner, repo=repo))

    whole_contributors = get_repo_contributors(owner, repo, whole)
    part_contributors = get_repo_contributors(owner, repo, part)
    print('   {part}/{whole} contributors for {pname}/{wname}'.format(
        part=len(part_contributors),
        whole=len(whole_contributors),
        pname=pname,
        wname=wname))
    if verbose:
        for contributor in part_contributors:
            print('      {contributor}'.format(contributor=contributor))

    all_pulls = filter_pulls(get_repo_pulls(owner, repo), part)
    our_pulls = get_our_pulls(all_pulls)
    total_count = len(all_pulls)
    our_count = len(our_pulls)
    other_count = total_count - our_count
    print('   {others}/{total} ({percentage}%) pull requests by others in {pname}'.format(
        others=other_count,
        total=total_count,
        percentage=math.floor((other_count/total_count) * 100),
        pname=pname))
    if verbose:
        for pull in all_pulls:
            if pull not in our_pulls:
                print('      {pull} [{login}]'.format(pull=pull['number'], login=pull['user']['login']))

    median = compute_median_review_duration(all_pulls, part)
    print('   {days} median number of days pull requests were in review in {pname}'.format(
        days=median,
        pname=pname))

    # median = compute_median_response_time(all_pulls)
    # print('   {duration} median response time for pull requests in {pname}'.format(
    #     duration=friendly_duration(median),
    #     pname=pname))

def report_all_repos(owner, timeframe):

    whole = range(timeframe['whole']['start'], timeframe['whole']['end'])
    part = range(timeframe['part']['start'], timeframe['part']['end'])
    wname = timeframe['whole']['label']
    pname = timeframe['part']['label']

    repos = get_cached_results('orgs/{org}/repos'.format(org=owner))
    print('\n{owner} ({count} repos)'.format(owner=owner, count=len(repos)))

    whole_contributors = get_org_contributors(owner, repos, whole)
    part_contributors = get_org_contributors(owner, repos, part)
    print('   {part}/{whole} contributors for {pname}/{wname}'.format(
        part=len(part_contributors),
        whole=len(whole_contributors),
        pname=pname,
        wname=wname))
    if verbose:
        for contributor in part_contributors:
            print('      {contributor}'.format(contributor=contributor))

    all_pulls = filter_pulls(get_org_pulls(owner, repos), part)
    our_pulls = get_our_pulls(all_pulls)
    total_count = len(all_pulls)
    our_count = len(our_pulls)
    other_count = total_count - our_count
    print('   {others}/{total} ({percentage}%) pull requests by others in {pname}'.format(
        others=other_count,
        total=total_count,
        percentage=math.floor((other_count/max(1, total_count)) * 100),
        pname=pname))
    if verbose:
        for pull in all_pulls:
            if pull not in our_pulls:
                print('      {pull} [{login}]'.format(pull=pull['number'], login=pull['user']['login']))

    median = compute_median_review_duration(all_pulls, part)
    print('   {days} median number of days pull requests were in review in {pname}'.format(
        days=median,
        pname=pname))

    # median = compute_median_response_time(all_pulls)
    # print('   {duration} median response time for pull requests in {pname}'.format(
    #     duration=friendly_duration(median),
    #     pname=pname))

def report():
    for project in projects:
        for timeframe in timeframes:
            parts = project.split('/')
            if len(parts) < 2:
                owner = parts[0]
                report_all_repos(owner, timeframe)
            else:
                owner = parts[0]
                repo = parts[1]
                report_single_repo(owner, repo, timeframe)

if __name__ == '__main__':
    read_token()
    report()
