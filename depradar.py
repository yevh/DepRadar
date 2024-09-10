import requests
import json
import os
import subprocess
import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
HEADERS = {'Authorization': f'token {GITHUB_TOKEN}'} if GITHUB_TOKEN else {}

def check_node():
    try:
        subprocess.run(['node', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(['npm', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Node.js and npm are installed.")
    except subprocess.CalledProcessError:
        print("Node.js/npm is not installed.")
        sys.exit(1)

def check_token():
    url = "https://api.github.com/user"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Invalid GitHub token: {response.status_code}")
        sys.exit(1)
    print("GitHub token is valid.")

def get_repos(org_name):
    url = f'https://api.github.com/orgs/{org_name}/repos'
    repos = []
    page = 1

    while True:
        response = requests.get(url, headers=HEADERS, params={'page': page, 'per_page': 100})
        if response.status_code != 200:
            print(f"Error fetching repos: {response.status_code}")
            return []
        
        data = response.json()
        if not data:
            break
        repos.extend([repo['name'] for repo in data if not repo['private']])
        page += 1

    return repos

def clone_repo(org_name, repo_name):
    repo_url = f'https://github.com/{org_name}/{repo_name}.git'
    repo_path = f'./{repo_name}'
    subprocess.run(['git', 'clone', '--depth=1', repo_url, repo_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return repo_path

def check_packages(repo_path):
    package_json_path = os.path.join(repo_path, 'package.json')
    package_lock_path = os.path.join(repo_path, 'package-lock.json')
    return os.path.exists(package_json_path), os.path.exists(package_lock_path)

def install_dep(repo_path):
    try:
        subprocess.run(['npm', 'install'], cwd=repo_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        print(f"Error install npm packages: {repo_path}")
        return False

def get_dep(repo_path):
    result = subprocess.run(['npm', 'ls', '--json'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        dependencies = json.loads(result.stdout)
        return dependencies.get('dependencies', {})
    except json.JSONDecodeError:
        print(f"Error decoding npm ls output: {repo_path}")
        return {}

def get_dep_package(repo_path):
    package_json_path = os.path.join(repo_path, 'package.json')
    try:
        with open(package_json_path, 'r') as f:
            package_data = json.load(f)
        return {**package_data.get('dependencies', {}), **package_data.get('devDependencies', {})}
    except (json.JSONDecodeError, FileNotFoundError):
        print(f"Error reading package.json: {repo_path}")
        return {}

def extract_dep(dependencies, level=0, parent=None):
    result = {}
    for dep_name, dep_info in dependencies.items():
        if isinstance(dep_info, str):
            result[dep_name] = {
                'version': dep_info,
                'level': level,
                'parent': parent,
                'dependencies': {}
            }
        else:
            result[dep_name] = {
                'version': dep_info.get('version'),
                'level': level,
                'parent': parent,
                'dependencies': extract_dep(dep_info.get('dependencies', {}), level + 1, dep_name)
            }
    return result

def process_repo(org_name, repo):
    print(f"Checking repository: {repo}")
    
    repo_path = clone_repo(org_name, repo)
    has_package_json, has_package_lock = check_packages(repo_path)
    
    result = {'name': repo, 'dependencies': {}}
    
    if has_package_json:
        print(f"Found package.json in {repo}")
        if has_package_lock:
            print(f"Found package-lock.json in {repo}")
        
        if install_dep(repo_path):
            dependencies = get_dep(repo_path)
            if dependencies:
                result['dependencies'] = extract_dep(dependencies)
            else:
                dependencies = get_dep_package(repo_path)
                result['dependencies'] = extract_dep(dependencies)
        else:
            dependencies = get_dep_package(repo_path)
            result['dependencies'] = extract_dep(dependencies)
    else:
        print(f"No package.json found: {repo}")
    
    subprocess.run(['rm', '-rf', repo_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    return result

def check_repos_parallel(org_name):
    repos = get_repos(org_name)
    if not repos:
        print(f"Failed to fetch repositories for org: {org_name}")
        return []

    all_results = []
    with ProcessPoolExecutor() as executor:
        futures = [executor.submit(process_repo, org_name, repo) for repo in repos]
        for future in as_completed(futures):
            all_results.append(future.result())

    return all_results

def count_dep(deps):
    direct = len(deps)
    transitive = sum(count_dep(dep['dependencies'])[0] for dep in deps.values())
    return direct, transitive

def get_npm_info(package_name):
    url = f"https://registry.npmjs.org/{package_name}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        latest_version = data.get('dist-tags', {}).get('latest')
        latest_info = data.get('versions', {}).get(latest_version, {})
        return {
            'name': package_name,
            'version': latest_version,
            'license': latest_info.get('license', 'N/A'),
            'unpacked_size': latest_info.get('dist', {}).get('unpackedSize', 'N/A'),
            'total_files': len(latest_info.get('files', [])),
            'last_publish': data.get('time', {}).get(latest_version, 'N/A'),
            'collaborators': len(data.get('maintainers', [])),
        }
    else:
        return {}

def get_npm_downloads(package_name):
    url = f"https://api.npmjs.org/downloads/point/last-month/{package_name}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('downloads', 0)
    else:
        return 0

def get_repo_status(repo_url):
    if not repo_url.startswith('https://github.com/'):
        return 'Unknown'
    
    url = repo_url.replace('https://github.com/', 'https://api.github.com/repos/')
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        return 'Archived' if data.get('archived', False) else 'Active'
    else:
        return 'Unknown'

def generate_report(results, org_name):
    npm_repos = [repo for repo in results if repo['dependencies']]
    npm_repos.sort(key=lambda x: len(x['dependencies']), reverse=True)
    
    total_repos = len(results)
    npm_repos_count = len(npm_repos)
    
    total_direct_deps = sum(count_dep(repo['dependencies'])[0] for repo in npm_repos)
    total_transitive_deps = sum(count_dep(repo['dependencies'])[1] for repo in npm_repos)
    
    repo_data_json = json.dumps({repo['name']: repo['dependencies'] for repo in npm_repos})
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{org_name} Dependency Report</title>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
         <style>
            body {{
                font-family: 'Inter', sans-serif;
                background-color: #f3f4f6;
            }}
            .node {{
                cursor: pointer;
            }}
            .node circle {{
                fill: #3B82F6;
                stroke: #2563EB;
                stroke-width: 2px;
            }}
            .node text {{
                font: 12px sans-serif;
            }}
            .link {{
                fill: none;
                stroke: #9CA3AF;
                stroke-width: 1px;
                stroke-opacity: 0.6;
            }}
            .table-header {{
                cursor: pointer;
            }}
            .table-header:hover {{
                background-color: #E5E7EB;
            }}
        </style>
    </head>
    <body class="bg-gray-100">
        <div class="container mx-auto px-4 py-8">
            <header class="bg-white shadow-lg rounded-lg mb-8 p-8">
                <h1 class="text-4xl font-bold text-gray-900 mb-2">Dependency Report</h1>
                <a href="https://github.com/{org_name}" target="_blank" class="text-2xl text-blue-600 hover:underline">
                    https://github.com/{org_name}
                </a>
            </header>

            <div class="bg-white shadow-lg rounded-lg p-8 mb-8">
                <h2 class="text-2xl font-semibold mb-6">Summary</h2>
                <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
                    <div class="bg-gradient-to-br from-blue-500 to-blue-600 p-6 rounded-lg text-white">
                        <p class="text-lg font-semibold mb-2">Total Repositories</p>
                        <p class="text-3xl font-bold">{total_repos}</p>
                    </div>
                    <div class="bg-gradient-to-br from-green-500 to-green-600 p-6 rounded-lg text-white">
                        <p class="text-lg font-semibold mb-2">NPM Repositories</p>
                        <p class="text-3xl font-bold">{npm_repos_count}</p>
                    </div>
                    <div class="bg-gradient-to-br from-yellow-500 to-yellow-600 p-6 rounded-lg text-white">
                        <p class="text-lg font-semibold mb-2">Direct Dependencies</p>
                        <p class="text-3xl font-bold">{total_direct_deps}</p>
                    </div>
                    <div class="bg-gradient-to-br from-red-500 to-red-600 p-6 rounded-lg text-white">
                        <p class="text-lg font-semibold mb-2">Transitive Dependencies</p>
                        <p class="text-3xl font-bold">{total_transitive_deps}</p>
                    </div>
                </div>
            </div>
            
            <div class="bg-white shadow rounded-lg p-6 mb-8">
                <h2 class="text-2xl font-semibold mb-4">Repository Analysis</h2>
                <select id="repo-select" class="block w-full bg-white border border-gray-300 rounded-md shadow-sm focus:border-blue-500 focus:ring-blue-500">
                    <option value="">Select a repository</option>
                    {' '.join(f'<option value="{repo["name"]}">{repo["name"]} ({len(repo["dependencies"])} dependencies)</option>' for repo in npm_repos)}
                </select>
            </div>
            
            <div id="repo-details" class="bg-white shadow rounded-lg p-6 mb-8 hidden">
                <h3 id="repo-name" class="text-2xl font-semibold mb-4"></h3>
                <div id="dependency-graph" class="w-full h-[600px] border border-gray-300 rounded-lg mb-8"></div>
                <div id="dependency-table" class="overflow-x-auto"></div>
            </div>
        </div>
        
        <script>
        const repoData = {repo_data_json};
        const GITHUB_TOKEN = '{GITHUB_TOKEN}';

        function createGraph(repoName) {{
            const data = repoData[repoName];
            const width = document.getElementById('dependency-graph').offsetWidth;
            const height = 600;
            
            const color = d3.scaleOrdinal(d3.schemeCategory10);
            
            const svg = d3.select("#dependency-graph")
                .append("svg")
                .attr("viewBox", [0, 0, width, height])
                .call(d3.zoom().on("zoom", (event) => g.attr("transform", event.transform)));
            
            const g = svg.append("g");
            
            const root = d3.hierarchy({{ name: repoName, children: Object.entries(data).map(([name, info]) => ({{ name, children: Object.entries(info.dependencies).map(([childName, childInfo]) => ({{ name: childName }})) }})) }});
            
            const links = root.links();
            const nodes = root.descendants();
            
            const simulation = d3.forceSimulation(nodes)
                .force("link", d3.forceLink(links).id(d => d.id).distance(100))
                .force("charge", d3.forceManyBody().strength(-500))
                .force("x", d3.forceX(width / 2))
                .force("y", d3.forceY(height / 2));
            
            const link = g.selectAll(".link")
                .data(links)
                .join("line")
                .attr("class", "link")
                .attr("stroke", d => color(d.target.depth));
            
            const node = g.selectAll(".node")
                .data(nodes)
                .join("g")
                .attr("class", "node")
                .call(drag(simulation));
            
            node.append("circle")
                .attr("r", d => 8 - d.depth * 1.5)
                .attr("fill", d => color(d.depth));
            
            node.append("text")
                .attr("dy", "0.31em")
                .attr("x", d => d.children ? -8 : 8)
                .attr("text-anchor", d => d.children ? "end" : "start")
                .text(d => d.data.name)
                .clone(true).lower()
                .attr("fill", "none")
                .attr("stroke", "white")
                .attr("stroke-width", 3);
            
            simulation.on("tick", () => {{
                link
                    .attr("x1", d => d.source.x)
                    .attr("y1", d => d.source.y)
                    .attr("x2", d => d.target.x)
                    .attr("y2", d => d.target.y);
                
                node.attr("transform", d => `translate(${{d.x}},${{d.y}})`);
            }});
            
            function drag(simulation) {{
                function dragstarted(event, d) {{
                    if (!event.active) simulation.alphaTarget(0.3).restart();
                    d.fx = d.x;
                    d.fy = d.y;
                }}
                
                function dragged(event, d) {{
                    d.fx = event.x;
                    d.fy = event.y;
                }}
                
                function dragended(event, d) {{
                    if (!event.active) simulation.alphaTarget(0);
                    d.fx = null;
                    d.fy = null;
                }}
                
                return d3.drag()
                    .on("start", dragstarted)
                    .on("drag", dragged)
                    .on("end", dragended);
            }}
        }}

        function formatDate(dateString) {{
            const options = {{ year: 'numeric', month: 'short', day: 'numeric' }};
            return new Date(dateString).toLocaleDateString(undefined, options);
        }}

        function createDependencyTable(repoName) {{
            const tableContainer = document.getElementById('dependency-table');
            tableContainer.innerHTML = '<h4 class="text-xl font-semibold mb-4">Dependencies</h4>';
            
            const table = document.createElement('table');
            table.className = 'min-w-full divide-y divide-gray-200';
            table.innerHTML = `
                <thead class="bg-gray-50">
                    <tr>
                        ${{['Name', 'Type', 'Downloads', 'Version', 'License', 'Size', 'Files', 'Published', 'Archived', 'Introduced By'].map(header => `
                            <th scope="col" class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider table-header cursor-pointer">
                                ${{header}}
                            </th>
                        `).join('')}}
                    </tr>
                </thead>
                <tbody class="bg-white divide-y divide-gray-200">
                </tbody>
            `;
            
            const tbody = table.querySelector('tbody');
            
            function addDependencyRow(name, info) {{
                const row = document.createElement('tr');
                row.innerHTML = `
                    <td class="px-3 py-2 whitespace-nowrap text-sm font-medium text-gray-900">
                        <a href="https://www.npmjs.com/package/${{name}}" target="_blank" class="text-blue-500 hover:underline">${{name}}</a>
                    </td>
                    <td class="px-3 py-2 whitespace-nowrap text-sm text-gray-500">${{info.level === 0 ? 'Direct' : 'Indirect'}}</td>
                    ${{Array(8).fill('<td class="px-3 py-2 whitespace-nowrap text-sm text-gray-500">Loading...</td>').join('')}}
                `;
                tbody.appendChild(row);
                
                Promise.all([
                    fetch(`https://registry.npmjs.org/${{name}}`).then(res => res.json()),
                    fetch(`https://api.npmjs.org/downloads/point/last-month/${{name}}`).then(res => res.json())
                ]).then(([npmData, downloadData]) => {{
                    const latestVersion = npmData['dist-tags'].latest;
                    const latestInfo = npmData.versions[latestVersion];
                    const cells = row.querySelectorAll('td');
                    
                    cells[2].textContent = downloadData.downloads.toLocaleString();
                    cells[3].textContent = latestVersion;
                    cells[4].textContent = latestInfo.license || 'N/A';
                    cells[5].textContent = latestInfo.dist.unpackedSize ? `${{(latestInfo.dist.unpackedSize / 1024).toFixed(2)}} KB` : 'N/A';
                    cells[6].textContent = latestInfo.dist.fileCount || 'N/A';
                    cells[7].textContent = formatDate(npmData.time[latestVersion]);
                    
                    const repoUrl = latestInfo.repository && latestInfo.repository.url
                        ? latestInfo.repository.url.replace('git+', '').replace('.git', '')
                        : '';
                    
                    if (repoUrl.startsWith('https://github.com/')) {{
                        fetch(repoUrl.replace('https://github.com/', 'https://api.github.com/repos/'), {{
                            headers: {{ 'Authorization': `token ${{GITHUB_TOKEN}}` }}
                        }})
                        .then(res => res.json())
                        .then(repoData => {{
                            cells[8].textContent = repoData.archived ? 'Yes' : 'No';
                        }})
                        .catch(() => {{
                            cells[8].textContent = 'Unknown';
                        }});
                    }} else {{
                        cells[8].textContent = 'N/A';
                    }}
                    
                    cells[9].textContent = info.parent || 'N/A';
                }}).catch(error => {{
                    console.error('Error:', error);
                    const cells = row.querySelectorAll('td');
                    for (let i = 2; i < cells.length; i++) {{
                        cells[i].textContent = 'Error';
                    }}
                }});
            }}
            
            function addDependencies(deps, parent = null) {{
                Object.entries(deps).forEach(([name, info]) => {{
                    info.parent = parent;
                    addDependencyRow(name, info);
                    if (info.dependencies) {{
                        addDependencies(info.dependencies, name);
                    }}
                }});
            }}
            
            addDependencies(repoData[repoName]);
            
            tableContainer.appendChild(table);
            
            const headers = table.querySelectorAll('th');
            headers.forEach((header, index) => {{
                header.addEventListener('click', () => {{
                    const rows = Array.from(tbody.querySelectorAll('tr'));
                    const direction = header.classList.contains('sort-asc') ? -1 : 1;
                    
                    rows.sort((a, b) => {{
                        const aValue = a.children[index].textContent;
                        const bValue = b.children[index].textContent;
                        
                        if (index === 2) {{ 
                            return direction * (parseInt(aValue.replace(/,/g, '')) - parseInt(bValue.replace(/,/g, '')));
                        }} else if (index === 5) {{ 
                            return direction * (parseFloat(aValue) - parseFloat(bValue));
                        }} else {{
                            return direction * aValue.localeCompare(bValue);
                        }}
                    }});
                    
                    tbody.append(...rows);
                    
                    headers.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
                                        header.classList.toggle('sort-asc', direction === 1);
                                        header.classList.toggle('sort-desc', direction === -1);
                                    }});
                                }});
                            }}

                            document.getElementById('repo-select').addEventListener('change', function() {{
                                const repoName = this.value;
                                if (repoName) {{
                                    document.getElementById('repo-name').textContent = repoName;
                                    document.getElementById('repo-details').classList.remove('hidden');
                                    document.getElementById('dependency-graph').innerHTML = '';
                                    document.getElementById('dependency-table').innerHTML = '';
                                    createGraph(repoName);
                                    createDependencyTable(repoName);
                                }} else {{
                                    document.getElementById('repo-details').classList.add('hidden');
                                }}
                            }});
                            </script>
                        </body>
                        </html>
                        """
                        
    with open('dependency_report.html', 'w') as f:
        f.write(html_content)
    
    print("HTML report generated: dependency_report.html")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Check GitHub org for JS dependencies.')
    parser.add_argument('org', help='The name of the GitHub org')
    args = parser.parse_args()

    check_node()
    check_token()
    results = check_repos_parallel(args.org)
    generate_report(results, args.org)