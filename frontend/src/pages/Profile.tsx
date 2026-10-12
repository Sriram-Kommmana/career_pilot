import { useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { Plus, Trash2, Sparkles } from "lucide-react";
import type { ProfileOut } from "@/types/api";

export function Profile() {
  const queryClient = useQueryClient();
  const profileQuery = useQuery({
    queryKey: ["profile"],
    queryFn: () => api.get<ProfileOut>("/profile"),
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["profile"] });
  }

  if (profileQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (profileQuery.isError) {
    return <ErrorState error={profileQuery.error} onRetry={() => profileQuery.refetch()} />;
  }
  const profile = profileQuery.data;
  if (!profile) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Profile</h1>
        <p className="mt-1 text-sm text-slate-500">
          What CareerPilot uses to draft applications. Nothing here is ever
          invented — unanswered questions are left for you to answer.
        </p>
      </div>

      <BasicInfoCard profile={profile} onSaved={invalidate} />
      <PreferencesCard profile={profile} onSaved={invalidate} />
      <WorkAuthorisationCard profile={profile} onSaved={invalidate} />
      <SkillsCard profile={profile} onChanged={invalidate} />
      <ProjectsCard profile={profile} onChanged={invalidate} />
      <ExperienceCard profile={profile} onChanged={invalidate} />
      <EducationCard profile={profile} onChanged={invalidate} />
      <CertificationsCard profile={profile} onChanged={invalidate} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Basic info
// ---------------------------------------------------------------------------

function BasicInfoCard({ profile, onSaved }: { profile: ProfileOut; onSaved: () => void }) {
  const [fullName, setFullName] = useState(profile.full_name ?? "");
  const [headline, setHeadline] = useState(profile.headline ?? "");
  const [phone, setPhone] = useState(profile.phone ?? "");
  const [location, setLocation] = useState(profile.location ?? "");
  const [github, setGithub] = useState(profile.github_url ?? "");
  const [linkedin, setLinkedin] = useState(profile.linkedin_url ?? "");
  const [portfolio, setPortfolio] = useState(profile.portfolio_url ?? "");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch("/profile", {
        full_name: fullName || null,
        headline: headline || null,
        phone: phone || null,
        location: location || null,
        github_url: github || null,
        linkedin_url: linkedin || null,
        portfolio_url: portfolio || null,
      }),
    onSuccess: onSaved,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Basic information</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="grid gap-4 sm:grid-cols-2"
        >
          <Field label="Full name">
            <Input value={fullName} onChange={(e) => setFullName(e.target.value)} />
          </Field>
          <Field label="Headline">
            <Input value={headline} onChange={(e) => setHeadline(e.target.value)} />
          </Field>
          <Field label="Phone">
            <Input value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <Field label="Location">
            <Input value={location} onChange={(e) => setLocation(e.target.value)} />
          </Field>
          <Field label="GitHub URL">
            <Input value={github} onChange={(e) => setGithub(e.target.value)} />
          </Field>
          <Field label="LinkedIn URL">
            <Input value={linkedin} onChange={(e) => setLinkedin(e.target.value)} />
          </Field>
          <Field label="Portfolio URL">
            <Input value={portfolio} onChange={(e) => setPortfolio(e.target.value)} />
          </Field>
          <div className="col-span-full flex items-center gap-3">
            <Button type="submit" isLoading={mutation.isPending}>
              Save
            </Button>
            {error && <span className="text-sm text-red-600">{error}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Preferences
// ---------------------------------------------------------------------------

function PreferencesCard({ profile, onSaved }: { profile: ProfileOut; onSaved: () => void }) {
  const [targetRoles, setTargetRoles] = useState(profile.target_roles.join(", "));
  const [locations, setLocations] = useState(profile.preferred_locations.join(", "));
  const [openToRemote, setOpenToRemote] = useState(
    profile.open_to_remote === null ? "" : profile.open_to_remote ? "true" : "false",
  );
  const [excludedCompanies, setExcludedCompanies] = useState(
    profile.excluded_companies.join(", "),
  );
  const [excludedRoles, setExcludedRoles] = useState(profile.excluded_roles.join(", "));
  const [minSalary, setMinSalary] = useState(profile.minimum_salary?.toString() ?? "");
  const [currency, setCurrency] = useState(profile.salary_currency ?? "");
  const [threshold, setThreshold] = useState(profile.notify_score_threshold);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch("/profile", {
        target_roles: splitList(targetRoles),
        preferred_locations: splitList(locations),
        open_to_remote: openToRemote === "" ? null : openToRemote === "true",
        excluded_companies: splitList(excludedCompanies),
        excluded_roles: splitList(excludedRoles),
        minimum_salary: minSalary ? Number(minSalary) : null,
        salary_currency: currency || null,
        notify_score_threshold: threshold,
      }),
    onSuccess: onSaved,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Search preferences</CardTitle>
        <CardDescription>Used by the hard filters and the match score.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="grid gap-4 sm:grid-cols-2"
        >
          <Field label="Target roles (comma-separated)">
            <Input value={targetRoles} onChange={(e) => setTargetRoles(e.target.value)} />
          </Field>
          <Field label="Preferred locations (comma-separated)">
            <Input value={locations} onChange={(e) => setLocations(e.target.value)} />
          </Field>
          <Field label="Open to remote?">
            <Select value={openToRemote} onChange={(e) => setOpenToRemote(e.target.value)}>
              <option value="">Not stated</option>
              <option value="true">Yes</option>
              <option value="false">No</option>
            </Select>
          </Field>
          <Field label="Minimum salary">
            <div className="flex gap-2">
              <Input
                type="number"
                value={minSalary}
                onChange={(e) => setMinSalary(e.target.value)}
                className="flex-1"
              />
              <Input
                value={currency}
                onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                maxLength={3}
                placeholder="INR"
                className="w-20"
              />
            </div>
          </Field>
          <Field label="Excluded companies (comma-separated)">
            <Input
              value={excludedCompanies}
              onChange={(e) => setExcludedCompanies(e.target.value)}
            />
          </Field>
          <Field label="Excluded role keywords (comma-separated)">
            <Input value={excludedRoles} onChange={(e) => setExcludedRoles(e.target.value)} />
          </Field>
          <div className="col-span-full">
            <Label>Notify me above match score: {Math.round(threshold * 100)}%</Label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="mt-2 w-full"
            />
          </div>
          <div className="col-span-full flex items-center gap-3">
            <Button type="submit" isLoading={mutation.isPending}>
              Save
            </Button>
            {error && <span className="text-sm text-red-600">{error}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Work authorisation — only ever what the user explicitly typed.
// ---------------------------------------------------------------------------

function WorkAuthorisationCard({ profile, onSaved }: { profile: ProfileOut; onSaved: () => void }) {
  const [authorised, setAuthorised] = useState(
    typeof profile.work_authorisation.authorised === "string"
      ? profile.work_authorisation.authorised
      : "",
  );
  const [sponsorship, setSponsorship] = useState(
    typeof profile.work_authorisation.requires_sponsorship === "string"
      ? profile.work_authorisation.requires_sponsorship
      : "",
  );
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      api.patch("/profile", {
        work_authorisation: {
          authorised: authorised || null,
          requires_sponsorship: sponsorship || null,
        },
      }),
    onSuccess: onSaved,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Work authorisation</CardTitle>
        <CardDescription>
          Never inferred from your location or anything else. Left blank, these
          questions appear as "needs your input" on every application.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            mutation.mutate();
          }}
          className="grid gap-4 sm:grid-cols-2"
        >
          <Field label="Are you authorised to work in your target location(s)?">
            <Input
              value={authorised}
              onChange={(e) => setAuthorised(e.target.value)}
              placeholder="e.g. Yes, I am a citizen"
            />
          </Field>
          <Field label="Will you now or in future require visa sponsorship?">
            <Input
              value={sponsorship}
              onChange={(e) => setSponsorship(e.target.value)}
              placeholder="e.g. No"
            />
          </Field>
          <div className="col-span-full flex items-center gap-3">
            <Button type="submit" isLoading={mutation.isPending}>
              Save
            </Button>
            {error && <span className="text-sm text-red-600">{error}</span>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

function SkillsCard({ profile, onChanged }: { profile: ProfileOut; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: () => api.post("/profile/skills", { name }),
    onSuccess: () => {
      setName("");
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add skill."),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/profile/skills/${id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Skills</CardTitle>
        <CardDescription>
          "Evidenced" is derived automatically from projects and experience that
          cite the skill — it isn't something you set directly.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {profile.skills.map((skill) => (
            <span
              key={skill.id}
              className="flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm"
            >
              {skill.name}
              {skill.has_evidence && (
                <Sparkles
                  className="h-3 w-3 text-brand-500"
                  aria-label="Evidenced by a project or role"
                />
              )}
              <button
                onClick={() => deleteMutation.mutate(skill.id)}
                className="text-slate-400 hover:text-red-600"
                aria-label={`Remove ${skill.name}`}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </span>
          ))}
          {profile.skills.length === 0 && (
            <p className="text-sm text-slate-400">No skills added yet.</p>
          )}
        </div>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            if (name.trim()) addMutation.mutate();
          }}
          className="flex gap-2"
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Python"
            className="max-w-xs"
          />
          <Button type="submit" size="sm" isLoading={addMutation.isPending}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </form>
        {error && <Alert variant="danger">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

function ProjectsCard({ profile, onChanged }: { profile: ProfileOut; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [skillsUsed, setSkillsUsed] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: () =>
      api.post("/profile/projects", {
        name,
        description: description || undefined,
        skills_used: splitList(skillsUsed),
      }),
    onSuccess: () => {
      setName("");
      setSkillsUsed("");
      setDescription("");
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add project."),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/profile/projects/${id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Projects</CardTitle>
        <CardDescription>
          The skills a project lists become evidence for the matching skill.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {profile.projects.map((project) => (
            <li
              key={project.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3"
            >
              <div>
                <p className="text-sm font-medium text-slate-900">{project.name}</p>
                {project.description && (
                  <p className="mt-0.5 text-sm text-slate-500">{project.description}</p>
                )}
                {project.skills_used.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {project.skills_used.map((s) => (
                      <Badge key={s} variant="brand">
                        {s}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => deleteMutation.mutate(project.id)}
                className="flex-shrink-0 text-slate-400 hover:text-red-600"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
          {profile.projects.length === 0 && (
            <p className="text-sm text-slate-400">No projects added yet.</p>
          )}
        </ul>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            if (name.trim()) addMutation.mutate();
          }}
          className="grid gap-2 sm:grid-cols-2"
        >
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Project name"
          />
          <Input
            value={skillsUsed}
            onChange={(e) => setSkillsUsed(e.target.value)}
            placeholder="Skills used (comma-separated)"
          />
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Short description (optional)"
            className="sm:col-span-2"
          />
          <Button type="submit" size="sm" isLoading={addMutation.isPending} className="sm:col-span-2">
            <Plus className="h-4 w-4" /> Add project
          </Button>
        </form>
        {error && <Alert variant="danger">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Experience
// ---------------------------------------------------------------------------

function ExperienceCard({ profile, onChanged }: { profile: ProfileOut; onChanged: () => void }) {
  const [company, setCompany] = useState("");
  const [title, setTitle] = useState("");
  const [skillsUsed, setSkillsUsed] = useState("");
  const [isCurrent, setIsCurrent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: () =>
      api.post("/profile/experience", {
        company,
        title,
        skills_used: splitList(skillsUsed),
        is_current: isCurrent,
      }),
    onSuccess: () => {
      setCompany("");
      setTitle("");
      setSkillsUsed("");
      setIsCurrent(false);
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add experience."),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/profile/experience/${id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Work experience</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {profile.experience_records.map((record) => (
            <li
              key={record.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3"
            >
              <div>
                <p className="text-sm font-medium text-slate-900">
                  {record.title} at {record.company}
                </p>
                {record.is_current && (
                  <Badge variant="success" className="mt-1">
                    Current
                  </Badge>
                )}
                {record.skills_used.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {record.skills_used.map((s) => (
                      <Badge key={s} variant="brand">
                        {s}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
              <button
                onClick={() => deleteMutation.mutate(record.id)}
                className="flex-shrink-0 text-slate-400 hover:text-red-600"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
          {profile.experience_records.length === 0 && (
            <p className="text-sm text-slate-400">No experience added yet.</p>
          )}
        </ul>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            if (company.trim() && title.trim()) addMutation.mutate();
          }}
          className="grid gap-2 sm:grid-cols-2"
        >
          <Input value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Company" />
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" />
          <Input
            value={skillsUsed}
            onChange={(e) => setSkillsUsed(e.target.value)}
            placeholder="Skills used (comma-separated)"
            className="sm:col-span-2"
          />
          <label className="flex items-center gap-2 text-sm text-slate-600 sm:col-span-2">
            <input
              type="checkbox"
              checked={isCurrent}
              onChange={(e) => setIsCurrent(e.target.checked)}
            />
            This is my current role
          </label>
          <Button type="submit" size="sm" isLoading={addMutation.isPending} className="sm:col-span-2">
            <Plus className="h-4 w-4" /> Add experience
          </Button>
        </form>
        {error && <Alert variant="danger">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Education
// ---------------------------------------------------------------------------

function EducationCard({ profile, onChanged }: { profile: ProfileOut; onChanged: () => void }) {
  const [institution, setInstitution] = useState("");
  const [degree, setDegree] = useState("");
  const [level, setLevel] = useState("UNKNOWN");
  const [error, setError] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: () => api.post("/profile/education", { institution, degree, level }),
    onSuccess: () => {
      setInstitution("");
      setDegree("");
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add education."),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/profile/education/${id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Education</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {profile.education_records.map((record) => (
            <li
              key={record.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3"
            >
              <div>
                <p className="text-sm font-medium text-slate-900">{record.institution}</p>
                <p className="text-sm text-slate-500">{record.degree}</p>
              </div>
              <button
                onClick={() => deleteMutation.mutate(record.id)}
                className="flex-shrink-0 text-slate-400 hover:text-red-600"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
          {profile.education_records.length === 0 && (
            <p className="text-sm text-slate-400">No education added yet.</p>
          )}
        </ul>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            if (institution.trim()) addMutation.mutate();
          }}
          className="grid gap-2 sm:grid-cols-3"
        >
          <Input
            value={institution}
            onChange={(e) => setInstitution(e.target.value)}
            placeholder="Institution"
          />
          <Input value={degree} onChange={(e) => setDegree(e.target.value)} placeholder="Degree" />
          <Select value={level} onChange={(e) => setLevel(e.target.value)}>
            <option value="UNKNOWN">Level unstated</option>
            <option value="HIGH_SCHOOL">High school</option>
            <option value="DIPLOMA">Diploma</option>
            <option value="BACHELORS">Bachelor's</option>
            <option value="MASTERS">Master's</option>
            <option value="DOCTORATE">Doctorate</option>
          </Select>
          <Button type="submit" size="sm" isLoading={addMutation.isPending} className="sm:col-span-3">
            <Plus className="h-4 w-4" /> Add education
          </Button>
        </form>
        {error && <Alert variant="danger">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Certifications
// ---------------------------------------------------------------------------

function CertificationsCard({ profile, onChanged }: { profile: ProfileOut; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [issuer, setIssuer] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: () => api.post("/profile/certifications", { name, issuer: issuer || undefined }),
    onSuccess: () => {
      setName("");
      setIssuer("");
      onChanged();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not add certification."),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/profile/certifications/${id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Certifications</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {profile.certifications.map((cert) => (
            <li
              key={cert.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3"
            >
              <div>
                <p className="text-sm font-medium text-slate-900">{cert.name}</p>
                {cert.issuer && <p className="text-sm text-slate-500">{cert.issuer}</p>}
              </div>
              <button
                onClick={() => deleteMutation.mutate(cert.id)}
                className="flex-shrink-0 text-slate-400 hover:text-red-600"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
          {profile.certifications.length === 0 && (
            <p className="text-sm text-slate-400">No certifications added yet.</p>
          )}
        </ul>
        <form
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            setError(null);
            if (name.trim()) addMutation.mutate();
          }}
          className="grid gap-2 sm:grid-cols-2"
        >
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" />
          <Input value={issuer} onChange={(e) => setIssuer(e.target.value)} placeholder="Issuer (optional)" />
          <Button type="submit" size="sm" isLoading={addMutation.isPending} className="sm:col-span-2">
            <Plus className="h-4 w-4" /> Add certification
          </Button>
        </form>
        {error && <Alert variant="danger">{error}</Alert>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}
